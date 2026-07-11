import streamlit as st
import pandas as pd
import yfinance as yf
import numpy as np
import plotly.graph_objects as go
from datetime import datetime, timedelta, date
import os
import json
import warnings
import re
import time
try:
    import zoneinfo
    TW_TZ = zoneinfo.ZoneInfo("Asia/Taipei")
except Exception:
    from datetime import timezone
    TW_TZ = timezone(timedelta(hours=8))
import requests
from bs4 import BeautifulSoup

warnings.filterwarnings('ignore')

# Set page config
st.set_page_config(
    page_title="JC投資組合前瞻性壓力測試與風險監控",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ------------------------------------------------------------
# 部署安全性：簡單的密碼驗證機制 (基於 st.secrets)
# ------------------------------------------------------------
def check_password():
    """Returns True if the user had the correct password."""
    try:
        if not st.secrets or "auth" not in st.secrets or "password" not in st.secrets["auth"]:
            return True
    except Exception:
        return True

    def password_entered():
        if st.session_state["password"] == st.secrets["auth"]["password"]:
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.text_input(
            "🔑 請輸入密碼解鎖 dashboard",
            type="password",
            on_change=password_entered,
            key="password",
        )
        return False
    elif not st.session_state["password_correct"]:
        st.text_input(
            "🔑 請輸入密碼解鎖 dashboard",
            type="password",
            on_change=password_entered,
            key="password",
        )
        st.error("😕 密碼錯誤，請重新輸入！")
        return False
    else:
        return True

if not check_password():
    st.stop()

ASSET_HISTORY_FILE_PATH = os.path.join(os.path.dirname(__file__), 'asset_history.csv')

def track_weekly_assets(total_assets, total_liability, stock_value, net_equity):
    """每週自動記錄一次資產狀況 (對齊週日版，週六日不開盤，週日最準確)"""
    today = date.today()
    # 自動歸整為當週的週日
    sunday_of_week = today + timedelta(days=(6 - today.weekday()))
    target_date_str = sunday_of_week.isoformat()
    
    new_row = {
        "Date": target_date_str,
        "Total_Assets": round(total_assets),
        "Total_Liability": round(total_liability),
        "Stock_Value": round(stock_value),
        "Net_Equity": round(net_equity)
    }
    
    trigger_rebuild = False
    if not os.path.exists(ASSET_HISTORY_FILE_PATH):
        trigger_rebuild = True
    else:
        try:
            df = pd.read_csv(ASSET_HISTORY_FILE_PATH)
            if df.empty or len(df) < 5 or df['Date'].nunique() < 5:
                trigger_rebuild = True
        except Exception:
            trigger_rebuild = True

    if trigger_rebuild:
        # A. 自癒重建邏輯 (保留已有日期，向最早日期往前補齊，對齊週日)
        existing_rows = []
        if os.path.exists(ASSET_HISTORY_FILE_PATH):
            try:
                raw_df = pd.read_csv(ASSET_HISTORY_FILE_PATH)
                raw_df['Date'] = raw_df['Date'].astype(str).str.replace(" (預估)", "").str.strip()
                existing_rows = raw_df.to_dict('records')
            except:
                pass
        
        base_date = sunday_of_week
        if existing_rows:
            try:
                dates_parsed = [datetime.strptime(r['Date'], '%Y-%m-%d').date() for r in existing_rows if '-' in r['Date']]
                if dates_parsed:
                    base_date = min(dates_parsed)
            except:
                pass
                
        demo_rows = []
        for i in range(4, 0, -1):
            mock_date = (base_date - timedelta(days=7 * i)).isoformat()
            demo_rows.append({
                "Date": mock_date,
                "Total_Assets": round(total_assets * (1.0 - 0.02 * i)),
                "Total_Liability": round(total_liability),
                "Stock_Value": round(stock_value * (1.0 - 0.02 * i)),
                "Net_Equity": round(net_equity * (1.0 - 0.02 * i))
            })
            
        df_rebuilt = pd.DataFrame(demo_rows)
        if existing_rows:
            df_rebuilt = pd.concat([df_rebuilt, pd.DataFrame(existing_rows)], ignore_index=True)
            
        df_rebuilt['Date'] = pd.to_datetime(df_rebuilt['Date'])
        df_rebuilt = df_rebuilt.sort_values(by='Date').drop_duplicates(subset=['Date'], keep='last').reset_index(drop=True)
        df_rebuilt['Date'] = df_rebuilt['Date'].dt.date.map(lambda x: x.isoformat())
        df_rebuilt.to_csv(ASSET_HISTORY_FILE_PATH, index=False)
        df = df_rebuilt
    else:
        df = pd.read_csv(ASSET_HISTORY_FILE_PATH)

    try:
        # B. 寫入或覆蓋本週日數據：若本週日已經存在，以最新數據覆蓋；否則追加一行
        df['Date'] = df['Date'].astype(str).str.replace(" (預估)", "").str.strip()
        if target_date_str in df['Date'].values:
            df.loc[df['Date'] == target_date_str, ["Total_Assets", "Total_Liability", "Stock_Value", "Net_Equity"]] = [
                new_row["Total_Assets"], new_row["Total_Liability"], new_row["Stock_Value"], new_row["Net_Equity"]
            ]
        else:
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        
        df = df.sort_values(by='Date').drop_duplicates(subset=['Date'], keep='last').reset_index(drop=True)
        df.to_csv(ASSET_HISTORY_FILE_PATH, index=False)
    except Exception:
        pass
    
    return df


CSV_FILE_PATH = os.path.join(os.path.dirname(__file__), 'portfolio_data.csv')

LOANS_FILE_PATH = os.path.join(os.path.dirname(__file__), 'loans_data.csv')

# ============================================================
# Dynamic Ticker name mapping from stocks_list.txt
# ============================================================
def load_stock_names():
    # 內建常規持有個股的中文名稱備援，確保 API 離線/鎖 IP 或缺乏 stocks_list.txt 時仍能秒速加載最重要個股，避免讀取超時
    names = {
        "REALIZED_CASH": "已實現現金",
        "2330": "台積電", "2330.TW": "台積電",
        "2454": "聯發科", "2454.TW": "聯發科",
        "2317": "鴻海", "2317.TW": "鴻海",
        "2337": "旺宏", "2337.TW": "旺宏",
        "3028": "力致", "3028.TW": "力致",
        "6187": "萬潤", "6187.TWO": "萬潤",
        "3037": "欣興", "3037.TW": "欣興",
        "3017": "奇鋐", "3017.TW": "奇鋐",
        "8086": "宏捷科", "8086.TWO": "宏捷科",
        "4749": "新應材", "4749.TWO": "新應材",
        "3680": "家登", "3680.TWO": "家登",
        "8021": "尖點", "8021.TW": "尖點",
        "3481": "群創", "3481.TW": "群創",
        "8438": "昶昕", "8438.TW": "昶昕",
        "3691": "碩禾", "3691.TWO": "碩禾",
        "2423": "固緯", "2423.TW": "固緯",
        "8147": "正淩", "8147.TWO": "正淩",
        "5284": "JPP-KY", "5284.TW": "JPP-KY",
        "2493": "揚博", "2493.TW": "揚博",
        "3023": "信邦", "3023.TW": "信邦",
        "6672": "騰輝電子-KY", "6672.TW": "騰輝電子-KY",
        "3044": "健鼎", "3044.TW": "健鼎",
        "6134": "萬旭", "6134.TWO": "萬旭",
        "3305": "昇貿", "3305.TW": "昇貿",
        "3550": "聯穎", "3550.TW": "聯穎",
        "2413": "環科", "2413.TW": "環科",
        "3577": "協易機", "3577.TWO": "協易機",
        "2428": "興勤", "2428.TW": "興勤",
        "6716": "應廣", "6716.TWO": "應廣",
        "8028": "昇陽半導體", "8028.TW": "昇陽半導體"
    }
    
    txt_path = os.path.join(os.path.dirname(__file__), 'stocks_list.txt')
    
    # 若檔案不存在或為空，自動自官方 API 抓取所有上市與上櫃股票代號並建立備援 stocks_list.txt，確保獨立運行
    if not os.path.exists(txt_path) or os.path.getsize(txt_path) == 0:
        try:
            fetched_dict = {}
            # 1. 獲取上市公司 (TWSE) - 使用合理 5.0 秒 timeout 兼顧成功率與啟動速度
            url_twse = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
            r_twse = requests.get(url_twse, timeout=5.0)
            if r_twse.status_code == 200:
                for item in r_twse.json():
                    code = item.get("Code", "").strip()
                    name = item.get("Name", "").strip()
                    if code and name and code.isdigit() and len(code) == 4:
                        fetched_dict[f"{code}.TW"] = name
            
            # 2. 獲取上櫃公司 (TPEx) - 使用 5.0 秒 timeout
            url_tpex = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
            r_tpex = requests.get(url_tpex, timeout=5.0)
            if r_tpex.status_code == 200:
                for item in r_tpex.json():
                    code = item.get("SecuritiesCompanyCode", "").strip()
                    name = item.get("CompanyName", "").strip()
                    if code and name and code.isdigit() and len(code) == 4:
                        fetched_dict[f"{code}.TWO"] = name
            
            if fetched_dict:
                # 寫入 stocks_list.txt (使用帶有 BOM 的 UTF-8-sig)
                try:
                    with open(txt_path, "w", encoding="utf-8-sig") as f:
                        for code, name in sorted(fetched_dict.items()):
                            f.write(f"{code},{name}\n")
                except Exception:
                    pass
                
                # 同時將記憶體中剛抓下來的名稱加載進 names
                for code, name in fetched_dict.items():
                    names[code] = name
                    names[code.split('.')[0]] = name
        except Exception:
            pass

    if os.path.exists(txt_path):
        try:
            with open(txt_path, 'r', encoding='utf-8-sig') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    # Split by comma, colon, tab or whitespace
                    parts = re.split(r'[,:\s\t]+', line)
                    if len(parts) >= 2:
                        ticker = parts[0].strip().upper()
                        name = parts[1].strip()
                        names[ticker] = name
                        if '.' not in ticker:
                            # Map suffixes automatically if missing in text file
                            names[ticker + ".TW"] = name
                            names[ticker + ".TWO"] = name
        except Exception:
            pass
    return names

STOCK_NAMES = load_stock_names()

# ============================================================
# Caching Data Loading
# ============================================================
@st.cache_data(ttl=900)
def load_market_data(tickers, min_lookback_days):
    today = datetime.now()
    end_date_str = today.strftime('%Y-%m-%d')
    start_date = today - timedelta(days=int(min_lookback_days))
    start_date_str = start_date.strftime('%Y-%m-%d')

    benchmark_tickers = ["^TWII"]
    all_tickers = list(set(tickers + benchmark_tickers))

    live_prices = {}
    try:
        tickers_objects = yf.Tickers(" ".join(all_tickers))
        for t in all_tickers:
            try:
                live_prices[t] = tickers_objects.tickers[t].fast_info['last_price']
            except Exception:
                live_prices[t] = None
    except Exception as e:
        st.sidebar.warning(f"⚠️ 即時報價快照獲取失敗: {e}")

    try:
        raw_download = yf.download(all_tickers, start=start_date_str, end=end_date_str, group_by='ticker', progress=False)
    except Exception as e:
        st.error(f"❌ 無法獲取歷史數據: {e}")
        return None, None

    hist_close = pd.DataFrame(index=raw_download.index)
    for t in all_tickers:
        if t in raw_download.columns.levels[0]:
            df_ticker = raw_download[t]
            hist_close[t] = df_ticker['Adj Close'] if 'Adj Close' in df_ticker.columns else df_ticker['Close']

    hist_close = hist_close.ffill().bfill()
    if hist_close.index.tz is not None:
        hist_close.index = hist_close.index.tz_localize(None)

    latest_prices = {}
    for t in all_tickers:
        if live_prices.get(t) is not None and not np.isnan(live_prices[t]):
            latest_prices[t] = float(live_prices[t])
        elif t in hist_close.columns:
            latest_prices[t] = float(hist_close[t].iloc[-1])
        else:
            latest_prices[t] = 0.0

    return latest_prices, hist_close

def get_portfolio_value_on_date(hist_df, portfolio_df, target_date_str):
    if hist_df is None or hist_df.empty:
        return 0.0
    try:
        target_dt = pd.to_datetime(target_date_str)
        idx = hist_df.index
        if target_dt in idx:
            chosen_dt = target_dt
        else:
            preceding = idx[idx <= target_dt]
            chosen_dt = preceding[-1] if not preceding.empty else idx[0]
            
        total_val = 0.0
        for _, row in portfolio_df[portfolio_df['Ticker'] != 'REALIZED_CASH'].iterrows():
            ticker = row['Ticker'].strip().upper()
            shares = float(row['Shares'])
            if ticker in hist_df.columns:
                price = float(hist_df.loc[chosen_dt, ticker])
                total_val += shares * price
        return total_val
    except Exception:
        return 0.0


# ============================================================
# Core Functions & Default Loans CSV
# ============================================================


def get_default_loans_data():
    if os.path.exists(LOANS_FILE_PATH):
        try:
            return pd.read_csv(LOANS_FILE_PATH)
        except Exception:
            pass
    # Default matching Scenario 3 config
    df_default = pd.DataFrame([
        {
            'Label': '信用貸款',
            'Type': 'Installment',
            'Principal': 1950000.0,
            'Annual_Rate': 2.28,
            'Start_Date': '2026-06-26',
            'Actual_Interest': 0.0,
            'Is_Margin': False,
            'Margin_Ratio_Baseline': 0.0,
            'Available_To_Borrow': 0.0,
            'Call_Threshold': 130.0,
            'Recover_Threshold': 166.0,
            'Liquidation_Threshold': 110.0,
            'Has_Open_Record': False
        },
        {
            'Label': '隨借隨還額度',
            'Type': 'LOC',
            'Principal': 3695853.0,
            'Annual_Rate': 6.45,
            'Start_Date': '2026-01-22',
            'Actual_Interest': 10688.0,
            'Is_Margin': True,
            'Margin_Ratio_Baseline': 231.0,
            'Available_To_Borrow': 1066357.0,
            'Call_Threshold': 130.0,
            'Recover_Threshold': 166.0,
            'Liquidation_Threshold': 110.0,
            'Has_Open_Record': False
        }
    ])
    df_default.to_csv(LOANS_FILE_PATH, index=False)
    return df_default

def get_margin_status(margin_ratio, call_threshold, recover_threshold,
                      liquidation_threshold, has_open_record):
    if margin_ratio is None:
        return {"status": "⚪ 無資料", "level": "none"}

    if liquidation_threshold is not None and margin_ratio < liquidation_threshold:
        return {
            "status": f"🔴🔴 斷頭風險：維持率 {margin_ratio:.1f}% 已低於斷頭線 {liquidation_threshold:.0f}%，可能隨時遭強制處分",
            "level": "liquidation",
        }

    if has_open_record:
        if margin_ratio >= recover_threshold:
            return {"status": "🟢 追繳記錄已解除（安全狀態）", "level": "safe"}
        elif margin_ratio >= call_threshold:
            return {
                "status": f"🟡 暫不處分，但仍有未解除的追繳記錄（維持率 {margin_ratio:.1f}%，一旦再跌破 {call_threshold:.0f}% 將次一營業日立即處分，無寬限期）",
                "level": "warning_with_record",
            }
        else:
            return {
                "status": f"🔴 危險：已有追繳記錄在身，且再度跌破 {call_threshold:.0f}%，次一營業日即處分擔保品",
                "level": "danger",
            }
    else:
        if margin_ratio >= call_threshold:
            tag = "🟢 安全" if margin_ratio >= recover_threshold else "🟡 正常但未達166%舒適區"
            return {"status": f"{tag}（維持率 {margin_ratio:.1f}%，無追繳記錄）", "level": "safe" if margin_ratio >= recover_threshold else "ok"}
        else:
            return {
                "status": f"🔴 危險：維持率 {margin_ratio:.1f}% 已跌破 {call_threshold:.0f}%，將收到追繳通知，2 個營業日內須補繳，否則第 3 個營業日起處分擔保品",
                "level": "danger",
            }

# ============================================================
# Title bar (Updated Title to JC, changed to 報告時間 with minute precision)
# ============================================================
col_title_left, col_title_right = st.columns([3, 1])
with col_title_left:
    st.markdown("<h2 style='margin-top: -30px; font-weight: 800;'>📊 JC投資組合前瞻性壓力測試與風險監控</h2>", unsafe_allow_html=True)
with col_title_right:
    now_tw = datetime.now(TW_TZ)
    st.markdown(
        f"<div style='text-align: right; margin-top: -15px; font-weight: bold; font-size:14px;'>"
        f"報告時間: <span style='font-size:12px; font-weight:normal;'>{now_tw.strftime('%Y-%m-%d %H:%M')} (台灣時間)</span>"
        f"</div>", 
        unsafe_allow_html=True
    )

# ============================================================
# Sidebar Configuration & Dynamic Loan Presets
# ============================================================
st.sidebar.markdown("### ⚙️ 系統參數與情境設定")

# Preset DB
SCENARIO_DATABASE = {
    1: {
        "current_cash": 1300000.0,
        "loans": [
            {
                'type': 'Installment',
                'principal': 2500000.0,
                'annual_rate': 0.0225,
                'start_date': '2026-05-22',
                'actual_interest': 4469.0,
                'label': '銀行信貸',
                'margin_loan': False,
            }
        ]
    },
    2: {
        "current_cash": -367645.0,
        "loans": [
            {
                'type': 'LOC',
                'label': '隨借隨還',
                'balance': 4405836.0,
                'actual_interest': 18561.0,
                'annual_rate': 0.0645,
                'start_date': '2026-01-12',
                'margin_loan': True,
                'margin_ratio': 188.0,
                'available_to_borrow': 19286.0,
                'call_threshold': 130.0,
                'recover_threshold': 166.0,
                'liquidation_threshold': 110.0,
                'has_open_margin_call_record': False,

            }
        ]
    },
    3: {
        "current_cash": -220000.0,
        "loans": [
            {
                'type': 'Installment',
                'principal': 1950000.0,
                'annual_rate': 0.0228,
                'start_date': '2026-06-26',
                'actual_interest': 0.0,
                'label': '信用貸款',
                'margin_loan': False,
                'margin_as_of_date': '2026-07-04'
            },
            {
                'type': 'LOC',
                'label': '隨借隨還額度',
                'balance': 3695853.0,
                'actual_interest': 10688.0,
                'annual_rate': 0.0645,
                'start_date': '2026-01-22',
                'margin_loan': True,
                'margin_ratio': 231.0,
                'available_to_borrow': 1066357.0,
                'call_threshold': 130.0,
                'recover_threshold': 166.0,
                'liquidation_threshold': 110.0,
                'has_open_margin_call_record': False,
                'margin_as_of_date': '2026-07-04',

            }
        ]
    },
    4: {
        "current_cash": 100000.0,
        "loans": []
    }
}

SCENARIO_OPTIONS = {
    1: "情境一：純信用貸款 / 本利攤還",
    2: "情境二：純隨借隨還 / 股票質押 LOC",
    3: "情境三：信貸 + 隨借隨還並存 (雙軌)",
    4: "情境四：完全無貸款 / 自有金流",
    0: "✏️ 自訂自創參數與借貸配置"
}

def _sync_loan_to_session(idx, row_data):
    """將貸款資料列同步寫入 session_state widget keys (統一入口，避免重複程式碼)"""
    st.session_state[f"l_label_{idx}"] = row_data.get('Label', '自訂貸款')
    st.session_state[f"l_p_{idx}"] = float(row_data.get('Principal', 0.0))
    st.session_state[f"l_r_{idx}"] = float(row_data.get('Annual_Rate', 0.0))
    st.session_state[f"l_i_{idx}"] = float(row_data.get('Actual_Interest', 0.0))
    st.session_state[f"l_margin_{idx}"] = bool(row_data.get('Is_Margin', False))
    st.session_state[f"l_ratio_base_{idx}"] = float(row_data.get('Margin_Ratio_Baseline', 180.0))
    st.session_state[f"l_avail_{idx}"] = float(row_data.get('Available_To_Borrow', 0.0))
    st.session_state[f"l_call_{idx}"] = float(row_data.get('Call_Threshold', 130.0))
    st.session_state[f"l_rec_{idx}"] = float(row_data.get('Recover_Threshold', 166.0))
    st.session_state[f"l_liq_{idx}"] = float(row_data.get('Liquidation_Threshold', 110.0))
    st.session_state[f"l_record_{idx}"] = bool(row_data.get('Has_Open_Record', False))
    st.session_state[f"l_start_{idx}"] = str(row_data.get('Start_Date', ''))

# Initialize scenario details state
if 'prev_scenario_id' not in st.session_state:
    if os.path.exists(LOANS_FILE_PATH):
        st.session_state.prev_scenario_id = 0
    else:
        st.session_state.prev_scenario_id = 2 # Default to Scenario 3

if 'current_cash' not in st.session_state:
    _cfg_path = os.path.join(os.path.dirname(__file__), 'app_config.json')
    try:
        if os.path.exists(_cfg_path):
            with open(_cfg_path, 'r', encoding='utf-8') as f:
                _cfg = json.load(f)
            st.session_state.current_cash = float(_cfg.get('current_cash', 0.0))
        else:
            st.session_state.current_cash = 0.0
    except Exception:
        st.session_state.current_cash = 0.0

chosen_scenario_id = st.sidebar.selectbox(
    "選擇資產情境模式 (載入後可於下方直接修改)",
    options=list(SCENARIO_OPTIONS.keys()),
    format_func=lambda x: SCENARIO_OPTIONS[x],
    index=list(SCENARIO_OPTIONS.keys()).index(st.session_state.prev_scenario_id) if st.session_state.prev_scenario_id in SCENARIO_OPTIONS else 2
)

if 'loans_df' not in st.session_state:
    st.session_state.loans_df = get_default_loans_data()
    for idx, row in st.session_state.loans_df.iterrows():
        _sync_loan_to_session(idx, row)

if chosen_scenario_id != st.session_state.prev_scenario_id:
    st.session_state.prev_scenario_id = chosen_scenario_id
    if chosen_scenario_id in SCENARIO_DATABASE:
        preset = SCENARIO_DATABASE[chosen_scenario_id]
        st.session_state.current_cash = preset["current_cash"]
        preset_loans = []
        for idx, l in enumerate(preset["loans"]):
            preset_loans.append({
                'Label': l.get('label', '自訂貸款'),
                'Type': l.get('type', 'Installment'),
                'Principal': float(l.get('principal', l.get('balance', 0.0))),
                'Annual_Rate': float(l.get('annual_rate', 0.0) * 100),
                'Start_Date': l.get('start_date', datetime.now().strftime('%Y-%m-%d')),
                'Actual_Interest': float(l.get('actual_interest', 0.0)),
                'Is_Margin': bool(l.get('margin_loan', False)),
                'Margin_Ratio_Baseline': float(l.get('margin_ratio', 0.0)),
                'Available_To_Borrow': float(l.get('available_to_borrow', 0.0)),
                'Call_Threshold': float(l.get('call_threshold', 130.0)),
                'Recover_Threshold': float(l.get('recover_threshold', 166.0)),
                'Liquidation_Threshold': float(l.get('liquidation_threshold', 110.0)),
                'Has_Open_Record': bool(l.get('has_open_margin_call_record', False))
            })
            _sync_loan_to_session(idx, preset_loans[-1])
            
        loans_df = pd.DataFrame(preset_loans)
        loans_df.to_csv(LOANS_FILE_PATH, index=False)
        st.session_state.loans_df = loans_df
        st.rerun()

# Sidebar editable parameters
st.sidebar.markdown("### 💵 現金調整")
_cash_input = st.sidebar.number_input(
    "手邊持有閒置現金 (NT$)",
    value=float(st.session_state.current_cash),
    step=10000.0,
    format="%.2f",
    key="cash_input_widget"
)
if _cash_input != st.session_state.current_cash:
    st.session_state.current_cash = _cash_input
if st.sidebar.button("💾 保存現金設定", key="save_cash_btn"):
    _cfg_path = os.path.join(os.path.dirname(__file__), 'app_config.json')
    try:
        cfg = {}
        if os.path.exists(_cfg_path):
            with open(_cfg_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
        cfg['current_cash'] = _cash_input
        with open(_cfg_path, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        st.session_state.current_cash = _cash_input
        st.sidebar.success("現金設定已保存！")
    except Exception as e:
        st.sidebar.error(f"保存失敗: {e}")

# We load active_stock_df here briefly to calculate price ratios for auto-margin updates
default_csv = pd.read_csv(CSV_FILE_PATH) if os.path.exists(CSV_FILE_PATH) else pd.DataFrame()
active_tickers_list = []
if not default_csv.empty and 'Ticker' in default_csv.columns:
    active_tickers_list = [t.strip().upper() for t in default_csv['Ticker'].tolist() if t.strip().upper() != 'REALIZED_CASH']


# Calculate lookback dynamically from the earliest loan start date to fetch enough history
earliest_date = date.today() - timedelta(days=90)
if not st.session_state.loans_df.empty:
    for _, row in st.session_state.loans_df.iterrows():
        if bool(row.get('Is_Margin', False)) and str(row.get('Start_Date', '')):
            try:
                sd = datetime.strptime(str(row['Start_Date']).strip(), '%Y-%m-%d').date()
                if sd < earliest_date:
                    earliest_date = sd
            except Exception:
                pass
lookback_days = (date.today() - earliest_date).days + 15
lookback_days = max(90, min(lookback_days, 365))

# Cache-friendly loading for scaling margin ratios
sc_prices = {}
sc_hist = pd.DataFrame()
if active_tickers_list:
    try:
        sc_prices, sc_hist = load_market_data(active_tickers_list, min_lookback_days=lookback_days)
    except Exception:
        pass

st.sidebar.markdown("### 🏦 編輯現有貸款內容")
if not st.session_state.loans_df.empty:
    # Pre-calculate current collateral portfolio value today
    val_now = 0.0
    for _, r in default_csv[default_csv['Ticker'] != 'REALIZED_CASH'].iterrows():
        tk = r['Ticker'].strip().upper()
        shs = float(r['Shares'])
        if active_tickers_list:
            try:
                val_now += shs * sc_prices.get(tk, 0.0)
            except Exception:
                pass

    for idx, row in st.session_state.loans_df.iterrows():
        with st.sidebar.expander(f"📝 {idx+1}. {row['Label']} ({row['Type']})", expanded=(idx==0)):
            # Safely initialize widget session state keys if not already present
            for key, val in [
                (f"l_label_{idx}", row['Label']),
                (f"l_p_{idx}", float(row['Principal'])),
                (f"l_r_{idx}", float(row['Annual_Rate'])),
                (f"l_i_{idx}", float(row['Actual_Interest'])),
                (f"l_margin_{idx}", bool(row['Is_Margin'])),
                (f"l_ratio_base_{idx}", float(row.get('Margin_Ratio_Baseline', 180.0))),
                (f"l_avail_{idx}", float(row.get('Available_To_Borrow', 0.0))),
                (f"l_call_{idx}", float(row.get('Call_Threshold', 130.0))),
                (f"l_rec_{idx}", float(row.get('Recover_Threshold', 166.0))),
                (f"l_liq_{idx}", float(row.get('Liquidation_Threshold', 110.0))),
                (f"l_record_{idx}", bool(row.get('Has_Open_Record', False))),
                (f"l_start_{idx}", str(row.get('Start_Date', datetime.now().strftime('%Y-%m-%d'))))
            ]:
                if key not in st.session_state:
                    st.session_state[key] = val

            new_label = st.text_input("貸款名稱標籤", value=str(st.session_state[f"l_label_{idx}"]), key=f"l_label_{idx}")
            new_principal = st.number_input("本金/餘額 (NT$)", min_value=0.0, step=50000.0, value=float(st.session_state[f"l_p_{idx}"]), key=f"l_p_{idx}")
            
            # Annual rate (typed as percent)
            new_rate = st.number_input(
                "年化利率 (%)",
                min_value=0.0,
                max_value=30.0,
                step=0.01,
                format="%.2f",
                value=float(st.session_state[f"l_r_{idx}"]),
                key=f"l_r_{idx}"
            )
            
            # 累積利息輸入 (適用於信貸及隨借隨還，皆會隨時間自動累計)
            new_interest = st.number_input("累積利息 (NT$)", min_value=0.0, step=1000.0, value=float(st.session_state[f"l_i_{idx}"]), key=f"l_i_{idx}")
            new_margin = st.checkbox("為股票質押維持率貸款", value=bool(st.session_state[f"l_margin_{idx}"]), key=f"l_margin_{idx}")
            
            # Start Date input field in the sidebar!
            new_start_date = st.text_input("起算日期 (YYYY-MM-DD)", value=str(st.session_state[f"l_start_{idx}"]), key=f"l_start_{idx}")
            
            # Pre-calculate estimated interest for helper text
            days_elapsed = 0
            interest_added = 0.0
            try:
                if new_start_date:
                    sd = datetime.strptime(new_start_date.strip(), '%Y-%m-%d').date()
                    days_elapsed = (date.today() - sd).days
                    if days_elapsed > 0:
                        interest_added = new_principal * (new_rate / 100.0) * (days_elapsed / 365.0)
            except Exception:
                pass
            est_total_interest = new_interest + interest_added
            st.caption(f"💡 目前累算總利息: **NT$ {est_total_interest:,.0f}** (已產生 NT$ {new_interest:,.0f} + 累計 {days_elapsed} 天利息)")
            
            if new_margin:
                new_ratio_base = st.number_input(
                    "維持率 (%)",
                    min_value=0.0,
                    step=0.1,
                    format="%.1f",
                    value=float(st.session_state[f"l_ratio_base_{idx}"]),
                    key=f"l_ratio_base_{idx}"
                )
                
                # 目前維持率直接採用輸入值
                projected_ratio = new_ratio_base
                
                # Display projected live维持率 as subtext to inform user
                st.caption(f"📈 目前維持率: **{projected_ratio:.1f}%**")
                
                # Auto calculate Available to Borrow if left 0
                calc_avail = float(st.session_state[f"l_avail_{idx}"])
                if calc_avail == 0.0:
                    calc_avail = max((new_principal * (projected_ratio / 100.0) * 0.6) - new_principal, 0.0)
                
                new_avail = st.number_input("尚可借額度 (NT$) (留0則自動計算)", min_value=0.0, step=10000.0, value=float(st.session_state[f"l_avail_{idx}"]), key=f"l_avail_{idx}")
                if new_avail == 0.0:
                    st.caption(f"💡 預估尚可借額度: **NT$ {calc_avail:,.0f}** (按6成成數估算)")
                    
                new_call = st.number_input("追繳線 (%)", min_value=0.0, value=float(st.session_state[f"l_call_{idx}"]), key=f"l_call_{idx}")
                new_rec = st.number_input("安全線 (%)", min_value=0.0, value=float(st.session_state[f"l_rec_{idx}"]), key=f"l_rec_{idx}")
                new_liq = st.number_input("斷頭線 (%)", min_value=0.0, value=float(st.session_state[f"l_liq_{idx}"]), key=f"l_liq_{idx}")
                new_record = st.checkbox("有未解除追繳紀錄", value=bool(st.session_state[f"l_record_{idx}"]), key=f"l_record_{idx}")
                
            if st.button("❌ 刪除此項貸款", key=f"l_del_{idx}"):
                st.session_state.loans_df = st.session_state.loans_df.drop(idx).reset_index(drop=True)
                for k in [f"l_label_{idx}", f"l_p_{idx}", f"l_r_{idx}", f"l_i_{idx}", f"l_margin_{idx}", f"l_ratio_base_{idx}", f"l_avail_{idx}", f"l_call_{idx}", f"l_rec_{idx}", f"l_liq_{idx}", f"l_record_{idx}", f"l_start_{idx}"]:
                    st.session_state.pop(k, None)
                st.session_state.prev_scenario_id = 0 # Switch to Custom!
                st.session_state.loans_df.to_csv(LOANS_FILE_PATH, index=False)
                st.rerun()

    # Display single save button for sidebar loans!
    if st.sidebar.button("💾 保存融資配置至 CSV", key="sidebar_loans_save_btn"):
        try:
            for idx, row in st.session_state.loans_df.iterrows():
                st.session_state.loans_df.at[idx, 'Label'] = st.session_state[f"l_label_{idx}"]
                st.session_state.loans_df.at[idx, 'Principal'] = float(st.session_state[f"l_p_{idx}"])
                st.session_state.loans_df.at[idx, 'Annual_Rate'] = float(st.session_state[f"l_r_{idx}"])
                st.session_state.loans_df.at[idx, 'Actual_Interest'] = float(st.session_state[f"l_i_{idx}"])
                st.session_state.loans_df.at[idx, 'Is_Margin'] = bool(st.session_state[f"l_margin_{idx}"])
                st.session_state.loans_df.at[idx, 'Start_Date'] = str(st.session_state[f"l_start_{idx}"])
                
                if bool(st.session_state[f"l_margin_{idx}"]):
                    st.session_state.loans_df.at[idx, 'Margin_Ratio_Baseline'] = float(st.session_state[f"l_ratio_base_{idx}"])
                    st.session_state.loans_df.at[idx, 'Available_To_Borrow'] = float(st.session_state[f"l_avail_{idx}"])
                    st.session_state.loans_df.at[idx, 'Call_Threshold'] = float(st.session_state[f"l_call_{idx}"])
                    st.session_state.loans_df.at[idx, 'Recover_Threshold'] = float(st.session_state[f"l_rec_{idx}"])
                    st.session_state.loans_df.at[idx, 'Liquidation_Threshold'] = float(st.session_state[f"l_liq_{idx}"])
                    st.session_state.loans_df.at[idx, 'Has_Open_Record'] = bool(st.session_state[f"l_record_{idx}"])
            
            st.session_state.prev_scenario_id = 0 # Switch to Custom!
            st.session_state.loans_df.to_csv(LOANS_FILE_PATH, index=False)
            st.sidebar.success("融資配置已保存至 CSV！")
            st.rerun()
        except Exception as e:
            st.sidebar.error(f"儲存失敗: {e}")
else:
    st.sidebar.caption("💡 目前無任何貸款設定。")

# Add new loan expander
with st.sidebar.expander("➕ 新增貸款項目"):
    new_type = st.selectbox("貸款類型", ["Installment (信貸)", "LOC (隨借隨還/質押)"])
    new_label = st.text_input("項目名稱", value="新融資項目", key="new_l_label")
    new_principal = st.number_input("融資額/本金 (NT$)", value=500000.0, step=50000.0, key="new_l_p")
    new_rate = st.number_input("年化利率 (%)", min_value=0.0, max_value=30.0, value=2.50, step=0.01, format="%.2f", key="new_l_r")
    # 新增貸款時統一命名為累積利息 (適用信貸與隨借隨還按日計息)
    new_interest = st.number_input("累積利息 (NT$)", value=0.0, key="new_l_i")
    new_margin = st.checkbox("此為股票维持率質押貸款", value=False, key="new_l_margin")
    
    new_ratio = 180.0
    new_avail = 0.0
    new_call = 130.0
    new_rec = 166.0
    new_liq = 110.0
    new_record = False
    
    if new_margin:
        new_ratio = st.number_input("維持率 (%)", value=180.0, key="new_l_ratio")
        new_avail = st.number_input("尚可借額度 (NT$) (留0則自動計算)", value=0.0, key="new_l_avail")
        new_call = st.number_input("追繳線 (%)", value=130.0, key="new_l_call")
        new_rec = st.number_input("解除線 (%)", value=166.0, key="new_l_rec")
        new_liq = st.number_input("斷頭線 (%)", value=110.0, key="new_l_liq")
        new_record = st.checkbox("已有追繳紀錄", value=False, key="new_l_record")
        
    if st.button("💾 儲存並新增融資項目", key="new_l_save"):
        new_row = {
            'Label': new_label,
            'Type': 'Installment' if 'Installment' in new_type else 'LOC',
            'Principal': new_principal,
            'Annual_Rate': new_rate,
            'Start_Date': datetime.now().strftime('%Y-%m-%d'),
            'Actual_Interest': new_interest,
            'Is_Margin': new_margin,
            'Margin_Ratio_Baseline': new_ratio,
            'Available_To_Borrow': new_avail,
            'Call_Threshold': new_call,
            'Recover_Threshold': new_rec,
            'Liquidation_Threshold': new_liq,
            'Has_Open_Record': new_record
        }
        st.session_state.loans_df = pd.concat([st.session_state.loans_df, pd.DataFrame([new_row])], ignore_index=True)
        st.session_state.prev_scenario_id = 0 # Switch to Custom!
        st.session_state.loans_df.to_csv(LOANS_FILE_PATH, index=False)
        st.success(f"已新增: {new_label}")
        st.rerun()

current_cash = st.session_state.current_cash

# Advanced config expander (Updated Risk Free label to Taiwan specific)
with st.sidebar.expander("🛠️ 進階模型設定"):
    min_lookback_days = st.number_input("Beta/Alpha 歷史追溯天數 (用於計算歷史與危機模式 Beta 以及 Jensen's Alpha)", value=90, min_value=20, max_value=365)
    
    # Updated default to 1.725% reflecting Taiwan Bank 1-Year Time Deposit Rate
    annual_rf = st.number_input(
        "政策與定存指標：台灣央行重貼現率 / 台灣銀行一年期定儲利率 (%)", 
        min_value=0.0, 
        max_value=10.0, 
        value=1.725, 
        step=0.005, 
        format="%.3f"
    ) / 100.0
    


# ============================================================
# Load and Verify CSV
# ============================================================

# ⚡ 突出顯示的強制清空快取同步股價按鈕，直接露在 Sidebar 最外層！
st.sidebar.markdown("---")
if st.sidebar.button("⚡ 強制清空快取並同步最新股價", use_container_width=True):
    st.cache_data.clear()
    st.sidebar.success("⏳ 快取已清空！正在向 Yahoo Finance 下載最新報價...")
    st.rerun()

# 📥 數據備份與安全繼承下載專區
st.sidebar.markdown("---")
st.sidebar.markdown("### 📥 數據備份與安全繼承")
st.sidebar.caption("💡 由於 Streamlit Cloud 重新部署會以 GitHub 代碼覆蓋雲端，建議在 debug/修改代碼前，在此下載最新 CSV 覆蓋到您的本機專案目錄中，再一起推送到 GitHub，即可永久繼承歷史數據！")

for label, path, filename in [
    ("📁 下載最新持股 CSV", CSV_FILE_PATH, "portfolio_data.csv"),
    ("📁 下載最新貸款 CSV", LOANS_FILE_PATH, "loans_data.csv"),
    ("📈 下載每週資產歷史 CSV", ASSET_HISTORY_FILE_PATH, "asset_history.csv")
]:
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f_csv:
                csv_data = f_csv.read()
            st.sidebar.download_button(
                label=label,
                data=csv_data,
                file_name=filename,
                mime="text/csv",
                use_container_width=True
            )
        except Exception:
            pass

def get_default_csv_data():
    if os.path.exists(CSV_FILE_PATH):
        try:
            return pd.read_csv(CSV_FILE_PATH)
        except Exception:
            pass
    return pd.DataFrame(columns=['Ticker', 'Buy_Date', 'Avg_Cost', 'Shares', 'Realized_Capital_Gains', 'Dividends_Received'])

if 'portfolio_df' not in st.session_state:
    st.session_state.portfolio_df = get_default_csv_data()

df = st.session_state.portfolio_df

# Clean formatting on loaded data (Stripping whitespaces and uppercase tickers)
if 'Ticker' in df.columns:
    df['Ticker'] = df['Ticker'].astype(str).str.strip().str.upper()

num_cols = ['Shares', 'Avg_Cost', 'Realized_Capital_Gains', 'Dividends_Received']
for col in num_cols:
    if col in df.columns:
        df[col] = df[col].astype(str).str.replace(',', '', regex=False)
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

# Process active stocks and realized values
total_realized_gains = df['Realized_Capital_Gains'].sum() if 'Realized_Capital_Gains' in df.columns else 0.0
total_dividends_received = df['Dividends_Received'].sum() if 'Dividends_Received' in df.columns else 0.0

active_stock_df = df[df['Ticker'] != 'REALIZED_CASH'].copy()
tickers = active_stock_df['Ticker'].tolist()

# ============================================================
# Fetch Yahoo Finance Market Data
# ============================================================
if not tickers:
    st.warning("⚠️ 庫存持股目前為空。請利用【快速編輯庫存與已實現現金數據】功能新增個股以進行完整分析。")
    latest_prices = {}
    hist_close = pd.DataFrame()
else:
    with st.spinner("⏳ 正在同步最新報價與計算歷史 Beta..."):
        latest_prices, hist_close = load_market_data(tickers, min_lookback_days)

if hist_close is not None and not hist_close.empty:
    # Calculations
    current_twii_index = latest_prices.get("^TWII", float(hist_close["^TWII"].iloc[-1]))
    twii_start_price = float(hist_close["^TWII"].iloc[0])
    twii_period_return = ((current_twii_index - twii_start_price) / twii_start_price) * 100

    # Daily Return reference
    prev_closes = {}
    for t in hist_close.columns:
        if len(hist_close) > 1:
            if hist_close.index[-1].date() == datetime.now().date():
                prev_closes[t] = float(hist_close[t].iloc[-2])
            else:
                prev_closes[t] = float(hist_close[t].iloc[-1])
        else:
            prev_closes[t] = float(hist_close[t].iloc[-1])

    twii_prev_close = prev_closes.get("^TWII", current_twii_index)
    twii_daily_return = ((current_twii_index - twii_prev_close) / twii_prev_close) * 100

    # Individual calculations
    active_stock_df['Current_Price'] = active_stock_df['Ticker'].map(latest_prices).astype(float).round(2)
    active_stock_df['Prev_Close'] = active_stock_df['Ticker'].map(prev_closes).astype(float)
    
    active_stock_df['Daily_Return(%)'] = ((active_stock_df['Current_Price'] - active_stock_df['Prev_Close']) / active_stock_df['Prev_Close']) * 100
    active_stock_df['Daily_PNL'] = (active_stock_df['Current_Price'] - active_stock_df['Prev_Close']) * active_stock_df['Shares']

    active_stock_df['Market_Value'] = active_stock_df['Shares'] * active_stock_df['Current_Price']
    active_stock_df['Total_Cost'] = active_stock_df['Shares'] * active_stock_df['Avg_Cost']
    active_stock_df['Unrealized_PNL'] = active_stock_df['Market_Value'] - active_stock_df['Total_Cost']
    active_stock_df['Unrealized_ROI(%)'] = (active_stock_df['Unrealized_PNL'] / active_stock_df['Total_Cost']) * 100

    # Add Stock Name mapping (safe uppercase matching)
    active_stock_df['股票名稱'] = active_stock_df['Ticker'].map(lambda x: STOCK_NAMES.get(x.strip().upper(), "未知個股"))

    total_stock_market_value = active_stock_df['Market_Value'].sum()
    total_portfolio_daily_pnl = active_stock_df['Daily_PNL'].sum()
    
    prev_total_stock_mv = total_stock_market_value - total_portfolio_daily_pnl
    total_portfolio_daily_return = (total_portfolio_daily_pnl / (prev_total_stock_mv + current_cash)) * 100 if (prev_total_stock_mv + current_cash) > 0 else 0.0

    total_cost_basis = active_stock_df['Total_Cost'].sum()
    total_assets_market_value = total_stock_market_value + current_cash
    active_stock_df['Weight(%)'] = (active_stock_df['Market_Value'] / total_assets_market_value) * 100

    # Beta Calculation
    recent_twii_returns = hist_close["^TWII"].pct_change().dropna()
    var_twii = recent_twii_returns.var()
    betas = {}

    for t in tickers:
        if t in hist_close.columns:
            recent_t_returns = hist_close[t].pct_change().dropna()
            aligned = pd.concat([recent_t_returns, recent_twii_returns], axis=1).dropna()
            if len(aligned) > 20 and var_twii > 0:
                cov = aligned.iloc[:, 0].cov(aligned.iloc[:, 1])
                betas[t] = cov / var_twii
            else:
                betas[t] = 1.0
        else:
            betas[t] = 1.0

    active_stock_df['Beta'] = active_stock_df['Ticker'].map(betas)
    portfolio_weighted_beta = (active_stock_df['Weight(%)'] / 100 * active_stock_df['Beta']).sum()

    # ------------------------------------------------------------
    # 🛡️ 危機模式 Beta (Crisis-Mode Beta / Stress Beta) 演算法
    # ------------------------------------------------------------
    panic_days = recent_twii_returns[recent_twii_returns <= -1.0]
    crisis_betas = {}
    
    if len(panic_days) >= 5:
        panic_indices = panic_days.index
        for t in tickers:
            if t in hist_close.columns:
                recent_t_returns = hist_close[t].pct_change().dropna()
                # 只對應抽取大盤重跌日的樣本來算
                t_panic = recent_t_returns.reindex(panic_indices).dropna()
                twii_panic_aligned = recent_twii_returns.loc[t_panic.index]
                
                if len(t_panic) >= 5 and twii_panic_aligned.var() > 0:
                    cov_panic = t_panic.cov(twii_panic_aligned)
                    calc_crisis_beta = cov_panic / twii_panic_aligned.var()
                    
                    hist_b = betas.get(t, 1.0)
                    # 崩盤時相關性向 1.0 收斂，與歷史 Beta 取最大與平均收斂值的 max
                    crisis_betas[t] = max(calc_crisis_beta, (hist_b + 1.2) / 2.0)
                else:
                    hist_b = betas.get(t, 1.0)
                    crisis_betas[t] = max(hist_b, (hist_b + 1.2) / 2.0)
            else:
                crisis_betas[t] = 1.0
    else:
        # 樣本不足時，採用防守性「關聯性收斂公式」
        for t in tickers:
            hist_b = betas.get(t, 1.0)
            crisis_betas[t] = max(hist_b, (hist_b + 1.2) / 2.0)
            
    active_stock_df['Crisis_Beta'] = active_stock_df['Ticker'].map(crisis_betas).round(2)
    portfolio_weighted_crisis_beta = (active_stock_df['Weight(%)'] / 100 * active_stock_df['Crisis_Beta']).sum()

    # Jensen's Alpha
    rf_period_return = annual_rf * (min_lookback_days / 365) * 100
    active_stock_df['Start_Price'] = active_stock_df['Ticker'].map(lambda t: float(hist_close[t].iloc[0]) if t in hist_close.columns else latest_prices.get(t, 0.0))
    
    total_stock_start_value = (active_stock_df['Shares'] * active_stock_df['Start_Price']).sum()
    portfolio_start_value = total_stock_start_value + current_cash
    portfolio_end_value = total_stock_market_value + current_cash
    
    portfolio_period_return = ((portfolio_end_value - portfolio_start_value) / portfolio_start_value) * 100 if portfolio_start_value > 0 else 0.0
    
    jensen_alpha_period = portfolio_period_return - (rf_period_return + portfolio_weighted_beta * (twii_period_return - rf_period_return))
    jensen_alpha_annual = jensen_alpha_period * (365 / min_lookback_days)

    # ------------------------------------------------------------
    # 📈 計算持股近 5 個交易日漲跌幅 (融合買入日期與持股成本 Avg_Cost)
    # ------------------------------------------------------------
    start_date_weekly = ""
    end_date_weekly = ""
    if hist_close is not None and not hist_close.empty:
        idx_lbl = -min(5, len(hist_close))
        start_date_weekly = hist_close.index[idx_lbl].strftime('%Y-%m-%d')
        end_date_weekly = hist_close.index[-1].strftime('%Y-%m-%d')

    weekly_returns = {}
    for idx_row, row in active_stock_df.iterrows():
        t = row['Ticker']
        if t == 'REALIZED_CASH':
            continue
        
        # 預設為 5 天前歷史收盤價
        price_prev = 0.0
        if t in hist_close.columns and len(hist_close[t]) >= 2:
            idx_lookback = -min(5, len(hist_close[t]))
            price_prev = float(hist_close[t].iloc[idx_lookback])
            
        # 檢查買入日期是否在近 7 天內 (即本週剛買，尚未享受整週歷史漲跌幅)
        buy_date_str = str(row.get('Buy_Date', ''))
        is_recent_buy = False
        try:
            if buy_date_str and buy_date_str.strip():
                bd = datetime.strptime(buy_date_str.strip(), '%Y-%m-%d').date()
                if (date.today() - bd).days <= 7:
                    is_recent_buy = True
        except Exception:
            pass
            
        # 如果是本週內剛買，基期 Prev_Price 強制改為使用者買入的成本均價 Avg_Cost！
        avg_cost = float(row.get('Avg_Cost', 0.0))
        if is_recent_buy and avg_cost > 0.0:
            price_prev = avg_cost
            
        price_now = float(row.get('Current_Price', 0.0))
        if price_now == 0.0 and t in latest_prices:
            price_now = float(latest_prices[t])
            
        if price_prev > 0.0 and price_now > 0.0:
            weekly_returns[t] = ((price_now - price_prev) / price_prev) * 100
        else:
            weekly_returns[t] = 0.0

    active_stock_df['Weekly_Return(%)'] = active_stock_df['Ticker'].map(weekly_returns).fillna(0.0)

    # 找出上漲超過 10% 以及下跌超過 10% 的股票群
    stocks_only = active_stock_df[active_stock_df['Ticker'] != 'REALIZED_CASH']
    display_gainers = []
    display_losers = []
    
    if not stocks_only.empty:
        # A. 領漲篩選
        gainers_over_10 = stocks_only[stocks_only['Weekly_Return(%)'] >= 10.0].sort_values(by='Weekly_Return(%)', ascending=False)
        if not gainers_over_10.empty:
            display_gainers = [r for _, r in gainers_over_10.iterrows()]
        else:
            top_g = stocks_only.sort_values(by='Weekly_Return(%)', ascending=False).iloc[0]
            if top_g['Weekly_Return(%)'] > 0:
                display_gainers = [top_g]
                
        # B. 領跌篩選
        losers_over_10 = stocks_only[stocks_only['Weekly_Return(%)'] <= -10.0].sort_values(by='Weekly_Return(%)', ascending=True)
        if not losers_over_10.empty:
            display_losers = [r for _, r in losers_over_10.iterrows()]
        else:
            top_l = stocks_only.sort_values(by='Weekly_Return(%)', ascending=True).iloc[0]
            if top_l['Weekly_Return(%)'] < 0:
                display_losers = [top_l]


    # ------------------------------------------------------------
    # 🏦 Dynamic and Calculated Loans sync logic
    # ------------------------------------------------------------
    loans = []
    
    # Pre-calculate current collateral portfolio value today
    val_now_main = 0.0
    for _, r in active_stock_df.iterrows():
        tk = r['Ticker'].strip().upper()
        shs = float(r['Shares'])
        val_now_main += shs * latest_prices.get(tk, 0.0)

    for idx, row in st.session_state.loans_df.iterrows():
        # Calculate days elapsed if Start_Date is present for auto interest calculation (All types accrue daily)
        start_date_str = str(row.get('Start_Date', ''))
        calculated_interest = float(row.get('Actual_Interest', 0.0))
        try:
            if start_date_str:
                sd = datetime.strptime(start_date_str.strip(), '%Y-%m-%d').date()
                days = (date.today() - sd).days
                if days > 0:
                    accrued = float(row['Principal']) * (float(row['Annual_Rate']) / 100.0) * (days / 365.0)
                    calculated_interest += accrued
        except Exception:
            pass
            
        # 直接使用使用者設定的目前維持率，不做基期縮放
        projected_ratio = float(row.get('Margin_Ratio_Baseline', 180.0))
        
        # Calculate projected available to borrow
        avail = float(row.get('Available_To_Borrow', 0.0))
        if avail == 0.0 and bool(row.get('Is_Margin', False)):
            # Auto calculate: max((Principal * Margin_Ratio * 0.6) - Principal, 0.0)
            avail = max((float(row['Principal']) * (projected_ratio / 100.0) * 0.6) - float(row['Principal']), 0.0)

        loans.append({
            'label': row.get('Label', '貸款'),
            'type': row.get('Type', 'Installment'),
            'principal': float(row.get('Principal', 0.0)),
            'balance': float(row.get('Principal', 0.0)),
            'drawn': float(row.get('Principal', 0.0)),
            'total_payable': float(row.get('Principal', 0.0)),
            'annual_rate': float(row.get('Annual_Rate', 0.0)) / 100.0,
            'actual_interest': calculated_interest,
            'margin_loan': bool(row.get('Is_Margin', False)),
            'margin_ratio': projected_ratio,
            'available_to_borrow': avail,
            'call_threshold': float(row.get('Call_Threshold', 130.0)),
            'recover_threshold': float(row.get('Recover_Threshold', 166.0)),
            'liquidation_threshold': float(row.get('Liquidation_Threshold', 110.0)),
            'has_open_margin_call_record': bool(row.get('Has_Open_Record', False))
        })

    # Debt and Interest calculations
    has_loan = len(loans) > 0
    has_margin_loan = any(loan.get('margin_loan') for loan in loans)

    total_interest_expense = sum(loan.get('actual_interest', 0.0) for loan in loans)
    total_active_debt = sum(
        loan.get('total_payable') if (loan.get('margin_loan') and loan.get('total_payable') is not None)
        else loan.get('drawn', loan.get('principal', 0.0))
        for loan in loans
    )
    annual_interest_burn_rate = sum((loan.get('drawn', loan.get('principal', 0.0)) * loan.get('annual_rate', 0.0)) for loan in loans)

    current_net_equity = max(total_assets_market_value - total_active_debt, 1.0)
    effective_stock_leverage_mv = total_stock_market_value / current_net_equity if current_net_equity > 0 else 1.0

    # Net Equity & ROE
    portfolio_roi = ((total_stock_market_value - total_cost_basis) / total_cost_basis * 100) if total_cost_basis > 0 else 0.0
    total_unrealized_pnl = active_stock_df['Unrealized_PNL'].sum()
    net_profit_accumulated = total_unrealized_pnl + total_realized_gains + total_dividends_received - total_interest_expense

    true_injected_capital = max(current_net_equity - net_profit_accumulated, 1.0)
    net_equity_roe = (net_profit_accumulated / true_injected_capital) * 100

    # Risk buffers
    wipeout_drop_pct = (current_net_equity / total_stock_market_value) * 100 if total_stock_market_value > 0 else 100.0
    hurdle_rate_roe = (annual_interest_burn_rate / current_net_equity) * 100 if current_net_equity > 0 else 0.0

    safe_cushion_weight = active_stock_df[active_stock_df['Unrealized_ROI(%)'] >= 10]['Weight(%)'].sum()
    warning_cushion_weight = active_stock_df[(active_stock_df['Unrealized_ROI(%)'] >= 0) & (active_stock_df['Unrealized_ROI(%)'] < 10)]['Weight(%)'].sum()
    danger_cushion_weight = active_stock_df[active_stock_df['Unrealized_ROI(%)'] < 0]['Weight(%)'].sum()

    # 四大象限判定邏輯演算已重構，由危機模式 Beta 壓力測試取代

    # ============================================================
    # Tabs Setup
    # ============================================================
    tab1, tab2 = st.tabs([
        "📊 投資組合資產與風險看板 (Assets & Risk Dashboard)", 
        "📡 觀測站即時重訊預警衛星 (Live MOPS Alerts)"
    ])

    with tab1:
        def render_metric_card(title, value, subtext="", value_color="#10b981"):
            with st.container(border=True):
                st.markdown(f"<div style='font-size:12px; color:gray; font-weight:600; text-transform:uppercase; letter-spacing:0.02em;'>{title}</div>", unsafe_allow_html=True)
                st.markdown(f"<h3 style='margin:5px 0; color:{value_color}; font-weight:800;'>{value}</h3>", unsafe_allow_html=True)
                st.markdown(f"<div style='font-size:13.5px; color:#555555; font-weight:500;'>{subtext}</div>", unsafe_allow_html=True)

        # ------------------------------------------------------------
        # 【第一部分：現有持股明細】(Chart on Left, styled table on Right)
        # ------------------------------------------------------------
        st.markdown("### 📋 【第一部分：現有持股明細】")
        # 繪製本週漲跌最多卡片 (100% 寬度長方形橫向 Pills 字卡，完美自適應空間)
        if display_gainers or display_losers:
            gainer_loser_html = ""
            
            # 領漲區 (寬度 100%)
            if display_gainers:
                date_range_str = f" ({start_date_weekly} ~ {end_date_weekly})" if start_date_weekly else ""
                title_g = f"📈 近 5 個交易日漲幅 10% 以上標的{date_range_str}" if any(r['Weekly_Return(%)'] >= 10.0 for r in display_gainers) else f"📈 近 5 個交易日持股領漲標的{date_range_str}"
                items_g_html = ""
                for r in display_gainers:
                    items_g_html += f'''
                    <span style="display: inline-block; background: rgba(0, 204, 102, 0.08); color: #00cc66; border: 1px solid rgba(0, 204, 102, 0.2); border-radius: 4px; padding: 4px 10px; margin: 4px; font-size: 14px; font-weight: bold;">
                        {r['股票名稱']} ({r['Ticker'].split('.')[0]}) +{r['Weekly_Return(%)']:.2f}%
                    </span>'''
                gainer_loser_html += f"""
                <div style="width: 100%; background: rgba(0, 204, 102, 0.04); border: 1px solid rgba(0, 204, 102, 0.12); border-radius: 8px; padding: 12px; border-left: 4px solid #00cc66; margin-bottom: 12px;">
                    <span style="font-size: 12px; color: var(--text-color); opacity: 0.7; font-weight: 600; display: block; margin-bottom: 6px;">{title_g}</span>
                    <div style="display: flex; flex-wrap: wrap; gap: 4px;">
                        {items_g_html}
                    </div>
                </div>
                """
            
            # 領跌區 (寬度 100%)
            if display_losers:
                date_range_str = f" ({start_date_weekly} ~ {end_date_weekly})" if start_date_weekly else ""
                title_l = f"📉 近 5 個交易日跌幅 10% 以上標的{date_range_str}" if any(r['Weekly_Return(%)'] <= -10.0 for r in display_losers) else f"📉 近 5 個交易日持股領跌標的{date_range_str}"
                items_l_html = ""
                for r in display_losers:
                    items_l_html += f'''
                    <span style="display: inline-block; background: rgba(255, 75, 75, 0.08); color: #ff4b4b; border: 1px solid rgba(255, 75, 75, 0.2); border-radius: 4px; padding: 4px 10px; margin: 4px; font-size: 14px; font-weight: bold;">
                        {r['股票名稱']} ({r['Ticker'].split('.')[0]}) {r['Weekly_Return(%)']:.2f}%
                    </span>'''
                gainer_loser_html += f"""
                <div style="width: 100%; background: rgba(255, 75, 75, 0.04); border: 1px solid rgba(255, 75, 75, 0.12); border-radius: 8px; padding: 12px; border-left: 4px solid #ff4b4b; margin-bottom: 15px;">
                    <span style="font-size: 12px; color: var(--text-color); opacity: 0.7; font-weight: 600; display: block; margin-bottom: 6px;">{title_l}</span>
                    <div style="display: flex; flex-wrap: wrap; gap: 4px;">
                        {items_l_html}
                    </div>
                </div>
                """
                
            # 壓縮 HTML，清除所有行首前導縮排與換行，完美防止 Markdown 將其誤判為 Code Block！
            compact_html = "".join([line.strip() for line in gainer_loser_html.split('\n')])
            st.markdown(compact_html, unsafe_allow_html=True)
            
        table_df = active_stock_df.sort_values(by='Weight(%)', ascending=False).copy()
        
        col_chart, col_table = st.columns([1, 2])
        
        with col_chart:
            pie_data = []
            for _, r in table_df.iterrows():
                pie_data.append({"Name": f"{r['股票名稱']} ({r['Ticker'].split('.')[0]})", "Value": r['Market_Value']})
            if current_cash > 0:
                pie_data.append({"Name": "閒置現金", "Value": current_cash})
            pie_df = pd.DataFrame(pie_data)
            
            fig = go.Figure(data=[go.Pie(
                labels=pie_df['Name'],
                values=pie_df['Value'],
                hole=.4,
                hoverinfo="label+percent+value",
                textinfo="none",
                marker=dict(colors=None)
            )])
            fig.update_layout(
                margin=dict(t=5, b=5, l=5, r=5),
                showlegend=True,
                legend=dict(orientation="v", yanchor="middle", y=0.5, xanchor="left", x=1.0),
                height=320,
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(0,0,0,0)'
            )
            st.plotly_chart(fig, use_container_width=True)
            
        with col_table:
            display_cols = ['Ticker', '股票名稱', 'Weight(%)', 'Beta', 'Avg_Cost', 'Current_Price', 'Daily_Return(%)', 'Daily_PNL', 'Unrealized_PNL', 'Unrealized_ROI(%)']
            st.dataframe(
                table_df[display_cols],
                column_config={
                    "Ticker": st.column_config.TextColumn("代號"),
                    "股票名稱": st.column_config.TextColumn("名稱"),
                    "Weight(%)": st.column_config.NumberColumn("權重", format="%.2f%%"),
                    "Beta": st.column_config.NumberColumn("Beta", format="%.2f"),
                    "Avg_Cost": st.column_config.NumberColumn("成本均價", format="NT$ %.2f"),
                    "Current_Price": st.column_config.NumberColumn("目前收盤價", format="NT$ %.2f"),
                    "Daily_Return(%)": st.column_config.NumberColumn("今日漲跌", format="%+.2f%%"),
                    "Daily_PNL": st.column_config.NumberColumn("今日損益", format="NT$ %+,.0f"),
                    "Unrealized_PNL": st.column_config.NumberColumn("未實現損益", format="NT$ %+,.0f"),
                    "Unrealized_ROI(%)": st.column_config.NumberColumn("未實現 ROI", format="%+.2f%%"),
                },
                hide_index=True,
                use_container_width=True,
                height=320
            )
        st.caption(f"👉 今日投資組合總損益: **NT$ {total_portfolio_daily_pnl:+,.0f} ({total_portfolio_daily_return:+.2f}%)** | 今日大盤: **{twii_daily_return:+.2f}%** (💡 註: 報價預設快取 15 分鐘。盤中若欲同步最新即時股價，請點選左側 Sidebar 最下方的【⚡ 強制清空快取並同步最新股價】按鈕)")

        # ------------------------------------------------------------
        # 【第二部分：資金與負債現狀】
        # ------------------------------------------------------------
        st.write("")
        st.markdown("### 💰 【第二部分：資金與負債現狀】")
        
        cap_cols = st.columns(5)
        with cap_cols[0]:
            render_metric_card("整體資產總市值 (A)", f"NT$ {total_assets_market_value:,.0f}", "現股 + 閒置現金", "#38bdf8")
        with cap_cols[1]:
            render_metric_card("庫存現股總市值", f"NT$ {total_stock_market_value:,.0f}", "持股總現值", "#38bdf8")
        with cap_cols[2]:
            render_metric_card("手邊持有閒置現金", f"NT$ {current_cash:,.0f}", "未動用現金", "#38bdf8")
        with cap_cols[3]:
            render_metric_card("實際融資負債總額", f"NT$ {total_active_debt:,.0f}", "全部貸款負債", "#fb7185")
        with cap_cols[4]:
            render_metric_card("目前投入本金淨資產 (E)", f"NT$ {current_net_equity:,.0f}", "這才是你真實身價", "#10b981")
        
        # AI Health Check: [資金防護力]
        cash_pct = (current_cash / total_assets_market_value * 100) if total_assets_market_value > 0 else 0
        if cash_pct < 0:
            st.info(f"💡 **[AI 資金防護力解讀]** 閒置現金僅 {cash_pct:.1f}%。子彈幾近滿載，處於全面曝險狀態。由於對回撤的容錯率降低，請嚴格執行個股的停損。")
        elif cash_pct < 5:
            st.info(f"💡 **[AI 資金防護力解讀]** 閒置現金比例 {cash_pct:.1f}%，幾乎全倉。遇到突發修正時反應空間有限，建議保留適量子彈。")
        else:
            st.info(f"💡 **[AI 資金防護力解讀]** 閒置現金 {cash_pct:.1f}%。攻守配置標準，既保有防禦彈性，亦不稀釋持股爆發力。")

        # Details + Dispersed AI check 3
        col_cap, col_loan = st.columns(2)
        with col_cap:
            with st.container(border=True):
                st.markdown("<h5 style='margin-top:0; color:#38bdf8; font-weight:700;'>💰 資金與利潤明細</h5>", unsafe_allow_html=True)
                st.write(f"• 歷史累積已實現利潤：**NT$ {total_realized_gains:,.0f}**")
                st.write(f"• 歷史累積已收受股利：**NT$ {total_dividends_received:,.0f}**")
                st.write(f"• 自動推導真實本金：**NT$ {true_injected_capital:,.0f}** (淨資產 - 累積總利潤)")
            
        with col_loan:
            with st.container(border=True):
                st.markdown("<h5 style='margin-top:0; color:#fb7185; font-weight:700;'>🏦 負債與利息明細</h5>", unsafe_allow_html=True)
                if has_loan:
                    st.write(f"• 年化利息壓力預估：**NT$ {annual_interest_burn_rate:,.0f} / 年**")
                    st.write(f"• 利息生息門檻 (Hurdle Rate)：**{hurdle_rate_roe:.2f}% / 年**")
                    st.write(f"• 目前累計利息支出：**NT$ {total_interest_expense:,.0f}**")
                    
                    st.warning(f"⚠️ **[AI 債務生息解讀]** 當前負債結構下，組合每年需額外創造 {hurdle_rate_roe:.2f}% 的本金報酬，用以平滑利息支出成本。")
                else:
                    st.write("• **無任何外部融資與利息壓力** (實際槓桿 1.00x，安全無負擔)")

        if has_margin_loan:
            st.write("")
            for loan in loans:
                if loan.get('margin_loan'):
                    m_ratio = loan.get('margin_ratio')
                    c_thresh = loan.get('call_threshold', 130.0)
                    r_thresh = loan.get('recover_threshold', 166.0)
                    l_thresh = loan.get('liquidation_threshold', 110.0)
                    h_record = loan.get('has_open_margin_call_record', False)
                    status_info = get_margin_status(m_ratio, c_thresh, r_thresh, l_thresh, h_record)
                    
                    level = status_info['level']
                    avail_borrow = loan.get('available_to_borrow', 0.0)
                    avail_str = f" | 💰 尚可借額度: NT$ {avail_borrow:,.0f}" if avail_borrow > 0.0 else ""
                    
                    if level == 'safe':
                        st.success(f"🟢 **{loan.get('label','股票質押')}** | 目前維持率: **{m_ratio:.1f}%** | {status_info['status']}{avail_str}")
                    elif level in ['warning_with_record', 'ok']:
                        st.warning(f"🟡 **{loan.get('label','股票質押')}** | 目前維持率: **{m_ratio:.1f}%** | {status_info['status']}{avail_str}")
                    else:
                        st.error(f"🚨 **{loan.get('label','股票質押')}** | 目前維持率: **{m_ratio:.1f}%** | {status_info['status']}{avail_str}")
                    
                    if m_ratio is not None:
                        col_proj1, col_proj2 = st.columns(2)
                        with col_proj1:
                            if m_ratio > r_thresh:
                                drop_r = (1 - (r_thresh / m_ratio)) * 100
                                st.markdown(
                                    f"<div style='font-size:15px; font-weight:600; padding:4px 0;'>"
                                    f"📉 質押擔保品市值再跌 <span style='color:#ef4444;'>{drop_r:.1f}%</span> ➔ 維持率將降至 {r_thresh:.0f}% (跌出舒適解除線)"
                                    f"</div>", 
                                    unsafe_allow_html=True
                                )
                        with col_proj2:
                            if m_ratio > c_thresh:
                                drop_c = (1 - (c_thresh / m_ratio)) * 100
                                st.markdown(
                                    f"<div style='font-size:15px; font-weight:600; padding:4px 0;'>"
                                    f"📉 質押擔保品市值再跌 <span style='color:#ef4444;'>{drop_c:.1f}%</span> ➔ 維持率將降至 {c_thresh:.0f}% (觸發追繳)"
                                    f"</div>", 
                                    unsafe_allow_html=True
                                )

        # ------------------------------------------------------------
        # 【第三部分：投資組合防線指標】
        # ------------------------------------------------------------
        st.write("")
        st.markdown("### 🏆 【第三部分：投資組合防線指標】")
        kpi_cols = st.columns(6)
        with kpi_cols[0]:
            render_metric_card("真實本金 ROE", f"{net_equity_roe:+.1f}%", f"本金: {true_injected_capital/10000:.0f}萬", "#10b981" if net_equity_roe >= 0 else "#ef4444")
        with kpi_cols[1]:
            render_metric_card("現股未實現 ROI", f"{portfolio_roi:+.1f}%", f"損益: {total_unrealized_pnl/10000:.0f}萬", "#10b981" if portfolio_roi >= 0 else "#ef4444")
        with kpi_cols[2]:
            render_metric_card("詹森 Alpha (年化)", f"{jensen_alpha_annual:+.1f}%", f"近 {min_lookback_days} 天", "#10b981" if jensen_alpha_annual >= 0 else "#ef4444")
        with kpi_cols[3]:
            render_metric_card("組合加權 Beta", f"{portfolio_weighted_beta:.2f}", f"連動度: {portfolio_weighted_beta:.1%}", "#38bdf8")
        with kpi_cols[4]:
            render_metric_card("實質股票槓桿", f"{effective_stock_leverage_mv:.2f}x", f"現股市值: {total_stock_market_value/10000:.0f}萬", "#f59e0b" if effective_stock_leverage_mv > 1.2 else "#38bdf8")
        with kpi_cols[5]:
            render_metric_card("本金歸零極限", f"-{wipeout_drop_pct:.1f}%", "現股下跌極限承受力", "#ef4444")

        # AI Health Check: [槓桿波動與狀態提示]
        if has_loan:
            expected_dd = 3 * portfolio_weighted_beta * effective_stock_leverage_mv
            if effective_stock_leverage_mv > 1.5:
                st.warning(f"⚠️ **[AI 槓桿波動評估]** 當前股票曝險槓桿達 {effective_stock_leverage_mv:.2f}x。進攻極其銳利，但在高 Beta 環境下，若大盤出現 3% 級別的單日修正，預估真實本金（ROE）將面臨約 {expected_dd:.1f}% 的同步縮水。")
            elif effective_stock_leverage_mv > 1.1:
                st.info(f"💡 **[AI 槓桿波動評估]** 目前槓桿 {effective_stock_leverage_mv:.2f}x，資產淨值的波動速度將是現股部位的 {effective_stock_leverage_mv:.2f} 倍，請維持對總資產維持率的日常監控。")
        else:
            st.info("💡 **[AI 槓桿狀態解讀]** 無融資與貸款狀態，零外部利息與維持率壓力，風險完全取決於持股本身的 Beta 與技術面走勢。")

        # ------------------------------------------------------------
        # 【第四部分：雙向極端壓力測試】
        # ------------------------------------------------------------
        st.write("")
        st.markdown("### 🛡️ 【第四部分：雙向極端壓力測試】")
        st.write("拖拉調整大盤在**連續跌停/修正波段**中的累積跌幅，即時演算您的本金損益與質押維持率的動態變化：")
        
        # 顯示危機 Beta 風控說明卡片，提示已啟動恐慌收斂模型
        st.info(f"💡 **[危機模式 Beta 已啟用]** 當前壓力測試已自動採用「恐慌收斂 Beta」(加權組合: **{portfolio_weighted_crisis_beta:.2f}**，高於承平時期 Beta: **{portfolio_weighted_beta:.2f}**)。此算法模擬了系統性大跌時低波動防禦股關聯性往上收斂的實務情境，估計結果更為保守安全。")
        
        sim_drop = st.slider("模擬大盤波段累積跌幅 (%)", min_value=0.0, max_value=30.0, value=3.0, step=1.0)
        
        # 🚀 壓力測試減損模擬全量替換為 portfolio_weighted_crisis_beta！
        sim_expected_dd = sim_drop * portfolio_weighted_crisis_beta * effective_stock_leverage_mv
        sim_portfolio_value_loss = total_stock_market_value * (sim_drop / 100 * portfolio_weighted_crisis_beta)
        sim_net_equity = max(current_net_equity - sim_portfolio_value_loss, 1.0)
        sim_roe = ((net_profit_accumulated - sim_portfolio_value_loss) / true_injected_capital) * 100
        
        sim_cols = st.columns(3)
        with sim_cols[0]:
            render_metric_card("模擬真實本金 ROE 變化", f"{sim_roe:+.2f}%", f"變動: {sim_roe - net_equity_roe:+.2f}%", "#10b981" if sim_roe >= 0 else "#ef4444")
        with sim_cols[1]:
            render_metric_card("估計資產價值減損 (NT$)", f"-NT$ {sim_portfolio_value_loss:,.0f}", f"預期波段回撤: -{sim_expected_dd:.2f}%", "#ef4444")
        with sim_cols[2]:
            render_metric_card("模擬真實淨資產 (E)", f"NT$ {sim_net_equity:,.0f}", f"變動: -NT$ {sim_portfolio_value_loss:,.0f}", "#38bdf8")
            
        if has_margin_loan and sim_drop > 0:
            st.write("**⚠️ 模擬融資維持率降幅預警：**")
            for loan in loans:
                if loan.get('margin_loan'):
                    m_ratio = loan.get('margin_ratio')
                    sim_ratio = m_ratio * (1 - (sim_drop / 100 * portfolio_weighted_crisis_beta))
                    c_thresh = loan.get('call_threshold', 130.0)
                    r_thresh = loan.get('recover_threshold', 166.0)
                    
                    if sim_ratio >= r_thresh:
                        st.success(f"🟢 **{loan.get('label')} 模擬維持率**: **{sim_ratio:.1f}%** (高於解除線 {r_thresh:.0f}%，維持安全狀態)")
                    elif sim_ratio >= c_thresh:
                        st.warning(f"🟡 **{loan.get('label')} 模擬維持率**: **{sim_ratio:.1f}%** (低於舒適解除線 {r_thresh:.0f}%，進入警戒狀態)")
                    else:
                        st.error(f"🚨 **{loan.get('label')} 模擬維持率**: **{sim_ratio:.1f}%** (低於追繳線 {c_thresh:.0f}%，面臨限期補足與斷頭處分風險！)")

        # ------------------------------------------------------------
        # 【第五部分：心理與防禦防線】
        # ------------------------------------------------------------
        st.write("")
        st.markdown("### 🏁 【第五部分：心理與防禦防線】")
        
        safe_stocks = active_stock_df[active_stock_df['Unrealized_ROI(%)'] >= 10]
        warning_stocks = active_stock_df[(active_stock_df['Unrealized_ROI(%)'] >= 0) & (active_stock_df['Unrealized_ROI(%)'] < 10)]
        danger_stocks = active_stock_df[active_stock_df['Unrealized_ROI(%)'] < 0]

        def get_subset_names_str(subset_df):
            if subset_df.empty:
                return "無"
            return ", ".join([f"{r['股票名稱']} ({r['Weight(%)']:.1f}%)" for _, r in subset_df.sort_values(by='Weight(%)', ascending=False).iterrows()])

        st.write(f"• **🟩 安全區 (獲利 > 10%)** : **{safe_cushion_weight:.1f}%** 的資金 — 包含持股：{get_subset_names_str(safe_stocks)}")
        st.write(f"• **🟨 警戒區 (獲利 0~10%)** : **{warning_cushion_weight:.1f}%** 的資金 — 包含持股：{get_subset_names_str(warning_stocks)}")
        st.write(f"• **🟥 危險區 (未實現虧損)** : **{danger_cushion_weight:.1f}%** 的資金 — 包含持股：{get_subset_names_str(danger_stocks)}")
        
        st.write("")
        # AI Health Check: [心理安全墊]
        st.success(f"🟩 **[AI 心理防線與策略建議]** 目前 {safe_cushion_weight:.1f}% 的資金已拉開 >10% 的利潤空間，利於放寬波動容忍度讓利潤奔跑。另有 {danger_cushion_weight:.1f}% 的部位處於未實現虧損，屬於需防守的區域，汰弱留強。")

        # ------------------------------------------------------------
        # 【每週資產歷史趨勢折線圖】
        # ------------------------------------------------------------
        st.markdown("---")
        st.markdown("### 📈 【每週資產歷史趨勢折線圖】")
        try:
            today_str = date.today().isoformat()
            total_assets_calc = total_stock_market_value + current_cash
            total_liability_calc = sum(float(l['principal']) if 'principal' in l else float(l.get('Principal', 0.0)) for l in loans)
            stock_value_calc = total_stock_market_value
            net_equity_calc = total_assets_calc - total_liability_calc
            
            hist_df = track_weekly_assets(
                total_assets=total_assets_calc,
                total_liability=total_liability_calc,
                stock_value=stock_value_calc,
                net_equity=net_equity_calc
            )
            
            assets_w = hist_df['Total_Assets'] / 10000
            equity_w = hist_df['Net_Equity'] / 10000
            stock_w = hist_df['Stock_Value'] / 10000
            liability_w = hist_df['Total_Liability'] / 10000
            
            # 建立用於 X 軸顯示的標籤 (CSV 保持純日期，圖表上動態將前 4 筆歷史預估資料標記 " (預估)")
            x_labels = []
            for idx_row, row_val in enumerate(hist_df['Date']):
                date_str = str(row_val).replace(" (預估)", "").strip()
                if idx_row < 4 and date_str != today_str:
                    x_labels.append(f"{date_str} (預估)")
                else:
                    x_labels.append(date_str)
            
            fig_trend = go.Figure()
            fig_trend.add_trace(go.Bar(
                x=x_labels, y=assets_w, 
                name='總資產 (Total Assets)', 
                marker_color='#38bdf8',
                text=[f"{val:.0f}萬" for val in assets_w],
                textposition='outside'
            ))
            fig_trend.add_trace(go.Bar(
                x=x_labels, y=equity_w, 
                name='資產淨值 (Net Equity)', 
                marker_color='#10b981',
                text=[f"{val:.0f}萬" for val in equity_w],
                textposition='outside'
            ))
            fig_trend.add_trace(go.Bar(
                x=x_labels, y=stock_w, 
                name='股票庫存 (Stock Value)', 
                marker_color='#fb7185',
                text=[f"{val:.0f}萬" for val in stock_w],
                textposition='outside'
            ))
            fig_trend.add_trace(go.Bar(
                x=x_labels, y=liability_w, 
                name='總負債 (Liabilities)', 
                marker_color='#f59e0b',
                text=[f"{val:.0f}萬" for val in liability_w],
                textposition='outside'
            ))
            
            fig_trend.update_layout(
                barmode='group',  # 設定為 Clustered Column Chart (群組柱狀圖)
                xaxis_title="紀錄日期",
                yaxis_title="金額 (萬元 NT$)",
                hovermode="x unified",
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(0,0,0,0)',
                height=420,
                margin=dict(t=40, b=30, l=10, r=10),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
            )
            # 自動刻度聚焦並設定細緻網格 (不強制從0開始以放大波動，移除 dtick 限制讓 Plotly 自動呈現最美觀刻度)
            fig_trend.update_yaxes(
                autorange=True,
                rangemode='normal',
                showgrid=True, 
                gridwidth=1, 
                gridcolor='rgba(128,128,128,0.15)',
                tickformat=".0f"
            )
            fig_trend.update_xaxes(type='category', showgrid=True, gridwidth=1, gridcolor='rgba(128,128,128,0.12)')
            st.plotly_chart(fig_trend, use_container_width=True)
        except Exception as e:
            st.error(f"無法繪製每週資產趨勢圖: {e}")


        # ------------------------------------------------------------
        # Editors section: Holdings & Loans CSV databases (Spreadsheet designs)
        # ------------------------------------------------------------
        # Spreadsheet editor for Portfolio Holdings
        with st.expander("✏️ 快速編輯庫存持股與已實現利益數據 (CSV)", expanded=False):
            raw_df_editor = st.session_state.portfolio_df.copy()
            cols_needed = ['Ticker', 'Buy_Date', 'Avg_Cost', 'Shares', 'Realized_Capital_Gains', 'Dividends_Received']
            for col in cols_needed:
                if col not in raw_df_editor.columns:
                    raw_df_editor[col] = 0.0 if col in ['Avg_Cost', 'Shares', 'Realized_Capital_Gains', 'Dividends_Received'] else ""
            raw_df_editor = raw_df_editor[cols_needed]
            
            edited_portfolio = st.data_editor(
                raw_df_editor,
                num_rows="dynamic",
                use_container_width=True,
                key="inline_editor_tab1_v10_holdings",
                column_config={
                    "Ticker": st.column_config.TextColumn("代號 (例如 2330.TW / REALIZED_CASH)"),
                    "Buy_Date": st.column_config.TextColumn("買入日期 (YYYY-MM-DD)"),
                    "Avg_Cost": st.column_config.NumberColumn("均價 (NT$)", min_value=0.0, format="%.2f"),
                    "Shares": st.column_config.NumberColumn("股數", min_value=0, step=1),
                    "Realized_Capital_Gains": st.column_config.NumberColumn("已實現利益 (NT$)", min_value=0.0, format="%.0f"),
                    "Dividends_Received": st.column_config.NumberColumn("已收股利 (NT$)", min_value=0.0, format="%.0f")
                }
            )
            
            if st.button("💾 保存庫存變更至 CSV"):
                try:
                    edited_portfolio.dropna(subset=['Ticker'], inplace=True)
                    edited_portfolio.to_csv(CSV_FILE_PATH, index=False)
                    st.session_state.portfolio_df = edited_portfolio
                    st.success("庫存數據已保存，系統正在重新載入計算...")
                    st.rerun()
                except Exception as e:
                    st.error(f"寫入 CSV 失敗: {e}")

        # Spreadsheet editor for Asset History (Honest manual logging/backfilling)
        with st.expander("✏️ 快速編輯與手動補登每週資產歷史數據 (CSV)", expanded=False):
            if os.path.exists(ASSET_HISTORY_FILE_PATH):
                try:
                    raw_hist_df = pd.read_csv(ASSET_HISTORY_FILE_PATH)
                    hist_cols_needed = ['Date', 'Total_Assets', 'Total_Liability', 'Stock_Value', 'Net_Equity']
                    for col in hist_cols_needed:
                        if col not in raw_hist_df.columns:
                            raw_hist_df[col] = 0
                    raw_hist_df = raw_hist_df[hist_cols_needed]
                    
                    edited_hist = st.data_editor(
                        raw_hist_df,
                        num_rows="dynamic",
                        use_container_width=True,
                        key="inline_editor_tab1_v10_asset_history",
                        column_config={
                            "Date": st.column_config.TextColumn("日期 (YYYY-MM-DD)"),
                            "Total_Assets": st.column_config.NumberColumn("總資產 (NT$)", min_value=0.0, format="%.0f"),
                            "Total_Liability": st.column_config.NumberColumn("總負債 (NT$)", min_value=0.0, format="%.0f"),
                            "Stock_Value": st.column_config.NumberColumn("股票庫存 (NT$)", min_value=0.0, format="%.0f"),
                            "Net_Equity": st.column_config.NumberColumn("淨資產 (NT$)", min_value=0.0, format="%.0f")
                        }
                    )
                    
                    if st.button("💾 保存歷史數據變更"):
                        try:
                            edited_hist.dropna(subset=['Date'], inplace=True)
                            edited_hist.to_csv(ASSET_HISTORY_FILE_PATH, index=False)
                            st.success("每週資產歷史數據變更已成功保存，折線圖已更新！")
                            st.rerun()
                        except Exception as e:
                            st.error(f"寫入 CSV 失敗: {e}")
                except Exception as e:
                    st.error(f"讀取歷史數據失敗: {e}")
            else:
                st.warning("⚠️ 歷史數據檔案尚未建立，請先等待看板初始化。")



    # ============================================================
    # Tab 2: Live MOPS Alerts Scraper Integration
    # ============================================================
    with tab2:
        st.markdown("### 📡 JC投資組合基本面預警衛星")

        
        active_tickers = active_stock_df['Ticker'].tolist()
        my_stocks_dynamic = [t.split('.')[0] for t in active_tickers if t != 'REALIZED_CASH']
        
        def get_stock_name_by_code(code):
            return STOCK_NAMES.get(code + ".TW", STOCK_NAMES.get(code + ".TWO", STOCK_NAMES.get(code, "未知個股")))

        monitor_list_display = [f"{get_stock_name_by_code(s)} ({s})" for s in my_stocks_dynamic]
        
        # 將說明與監控標的資訊集中合併到同一個 HTML 框，使用自適應字體顏色變數 var(--text-color)
        st.markdown(f"""
            <div style="background: rgba(128, 128, 128, 0.06); border: 1px solid rgba(128, 128, 128, 0.12); border-radius: 8px; padding: 15px; margin-bottom: 18px; margin-top: 10px; line-height: 1.6;">
                <span style="font-size: 15px; color: var(--text-color); opacity: 0.9; display: block; margin-bottom: 8px; font-weight: 500;">對接「公開資訊觀測站 (MOPS)」，自動掃描持股個股近 30 天重大訊息，今日即時重訊優先置頂顯示。</span>
                <span style="font-size: 13px; color: var(--text-color); opacity: 0.7;">📋 <b>監控股票數</b>：{len(my_stocks_dynamic)} 檔 | 🔍 <b>掃描範圍</b>：近 30 天重大訊息 | 🌐 <b>資料來源</b>：MOPS 公開資訊觀測站</span>
            </div>
        """, unsafe_allow_html=True)
        
        with st.expander(f"📋 目前監控個股清單 (共 {len(my_stocks_dynamic)} 檔)", expanded=False):
            st.write(", ".join(monitor_list_display))
        
        def parse_to_date_object(date_str):
            try:
                match = re.search(r"(\d{3})/(\d{2})/(\d{2})", date_str)
                if not match:
                    return None
                tw_year, month, day = map(int, match.groups())
                return date(tw_year + 1911, month, day)
            except:
                return None

        def is_within_last_30_days(target_date):
            if not target_date:
                return False
            today = date.today()
            delta_days = (today - target_date).days
            return 0 <= delta_days <= 30



        def fetch_stock_news_requests_fallback(stock_code):
            """免瀏覽器核心的 HTTP 輕量級綜合重訊與除權息爬蟲 (當 Playwright 崩潰時自動降級備援使用)"""
            today_date_obj = date.today()
            current_tw_year_str = str(today_date_obj.year - 1911)
            
            session = requests.Session()
            headers_get = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            }
            try:
                session.get("https://mopsov.twse.com.tw/mops/web/t146sb05", headers=headers_get, timeout=6)
            except:
                pass
                
            headers_post = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://mops.twse.com.tw/mops/web/t146sb05",
                "Content-Type": "application/x-www-form-urlencoded",
            }
            
            endpoints = [
                "https://mopsov.twse.com.tw/mops/web/ajax_t146sb05",
                "https://mops.twse.com.tw/mops/web/ajax_t146sb05"
            ]
            
            post_data = {
                "encodeURIComponent": "1",
                "step": "1",
                "firstin": "1",
                "off": "1",
                "keyword4": "",
                "code1": "",
                "TYPEK2": "",
                "checkbtn": "",
                "queryName": "co_id",
                "inpuType": "co_id",
                "TYPEK": "all",
                "co_id": stock_code,
                "year": current_tw_year_str,
            }
            
            found_news = []
            for url in endpoints:
                try:
                    headers_post["Referer"] = url.replace("/ajax_", "/")
                    resp = session.post(url, data=post_data, headers=headers_post, timeout=10)
                    if resp.status_code != 200:
                        continue
                    
                    soup = BeautifulSoup(resp.text, "html.parser")
                    lines = []
                    for tr in soup.find_all("tr"):
                        tds = tr.find_all("td")
                        if tds:
                            line_text = "   ".join([td.get_text(strip=True) for td in tds])
                            lines.append(line_text)
                    
                    has_data = False
                    for line in lines:
                        clean = line.strip()
                        if not clean or "詳細資料" in clean or "主旨" in clean:
                            continue
                        
                        dates = re.findall(r"\d{3}/\d{2}/\d{2}", clean)
                        numbers = re.findall(r"\d+\.\d+", clean)
                        
                        if len(dates) == 2 and len(numbers) >= 2:
                            formatted = f"{dates[1]}  【股利分派公告】股東會日期: {dates[1]} | 盈餘分配之股票股利: {float(numbers[1]):.2f}元 | 除權/除息交易日: (請參閱除權息行) | 董事會決議日: {dates[0]} | 現金股利: {float(numbers[0]):.2f}元"
                            date_obj = parse_to_date_object(dates[1])
                            if date_obj and is_within_last_30_days(date_obj):
                                found_news.append({
                                    "text": formatted,
                                    "is_today": (date_obj == today_date_obj),
                                    "date": date_obj
                                })
                                has_data = True
                        elif len(dates) == 3:
                            formatted = f"{dates[1]}  【除權息公告】除權/除息交易日: {dates[1]} | 權利分派基準日: {dates[0]} | 現金股利發放日: {dates[2]}"
                            date_obj = parse_to_date_object(dates[1])
                            if date_obj and is_within_last_30_days(date_obj):
                                found_news.append({
                                    "text": formatted,
                                    "is_today": (date_obj == today_date_obj),
                                    "date": date_obj
                                })
                                has_data = True
                        elif f"{current_tw_year_str}/" in clean and len(clean) > 10:
                            if "請輸入" in clean or "公司代碼" in clean or "歷史查詢" in clean:
                                continue
                            clean_formatted = clean.replace("\xa0", " ").strip()
                            date_obj = parse_to_date_object(clean_formatted)
                            if date_obj and is_within_last_30_days(date_obj):
                                found_news.append({
                                    "text": clean_formatted,
                                    "is_today": (date_obj == today_date_obj),
                                    "date": date_obj
                                })
                                has_data = True
                                
                    if has_data:
                        break
                except Exception:
                    continue
            
            seen = set()
            unique_news = []
            for item in found_news:
                if item["text"] not in seen:
                    seen.add(item["text"])
                    unique_news.append(item)
            return unique_news

        def run_scraper(stocks_list, status_placeholder, progress_bar):
            results = []
            total = len(stocks_list)
            for i, stock in enumerate(stocks_list):
                news = fetch_stock_news_requests_fallback(stock)
                results.append((stock, news))
                percent = (i + 1) / total
                status_placeholder.write(f"⏳ **[HTTP 備援引擎]** 已完成掃描個股：{get_stock_name_by_code(stock)} ({stock}) (進度: {i+1}/{total})")
                progress_bar.progress(percent)
                time.sleep(0.3)
            return results

        if st.button("📡 啟動即時公開資訊觀測站重訊掃描"):
            if not my_stocks_dynamic:
                st.warning("⚠️ 庫存中無個股，無法執行掃描任務。")
            else:
                status_placeholder = st.empty()
                progress_bar = st.progress(0.0)
                with st.spinner("⚡ 正在發動輕量級重訊極速引擎，全速觀測中..."):
                    results = run_scraper(my_stocks_dynamic, status_placeholder, progress_bar)
                
                progress_bar.empty()
                status_placeholder.empty()
                
                total_alerts = 0
                today_alerts = 0
                alert_stocks = []
                clean_stocks = []
                
                for stock, news_list in results:
                    if news_list:
                        alert_stocks.append((stock, news_list))
                        total_alerts += len(news_list)
                        today_alerts += sum(1 for item in news_list if item["is_today"])
                    else:
                        clean_stocks.append(stock)
                
                col_sum1, col_sum2 = st.columns(2)
                with col_sum1:
                    st.markdown(f"""
                    <div style='background: linear-gradient(135deg, rgba(56, 189, 248, 0.04) 0%, rgba(56, 189, 248, 0.08) 100%);
                                padding: 18px 20px; border-radius: 8px; border: 1px solid rgba(56, 189, 248, 0.15);
                                border-left: 5px solid #38bdf8; margin-bottom: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.02);'>
                        <div style='font-size: 12px; color: var(--text-color); opacity: 0.7; font-weight: 600;'>近 30 天重要訊息總數</div>
                        <div style='font-size: 26px; font-weight: 800; margin: 4px 0; color: #38bdf8;'>{total_alerts} 筆</div>
                        <div style='font-size: 12px; color: var(--text-color); opacity: 0.65;'>30天內公開觀測站重大訊息總計</div>
                    </div>
                    """, unsafe_allow_html=True)
                with col_sum2:
                    st.markdown(f"""
                    <div style='background: linear-gradient(135deg, rgba(255, 75, 75, 0.04) 0%, rgba(255, 75, 75, 0.08) 100%);
                                padding: 18px 20px; border-radius: 8px; border: 1px solid rgba(255, 75, 75, 0.15);
                                border-left: 5px solid #ff4b4b; margin-bottom: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.02);'>
                        <div style='font-size: 12px; color: var(--text-color); opacity: 0.7; font-weight: 600;'>今日最新即時發布</div>
                        <div style='font-size: 26px; font-weight: 800; margin: 4px 0; color: #ff4b4b;'>{today_alerts} 筆</div>
                        <div style='font-size: 12px; color: var(--text-color); opacity: 0.65;'>今日新公布之即時重訊</div>
                    </div>
                    """, unsafe_allow_html=True)
                
                st.markdown("---")
                
                alert_stocks_sorted = sorted(
                    alert_stocks,
                    key=lambda x: (any(n["is_today"] for n in x[1]), len(x[1])),
                    reverse=True
                )
                
                if alert_stocks_sorted:
                    st.markdown("#### 🎯 攔截重訊個股明細 (🔥 今日發布個股已置頂)")
                    for stock, news_list in alert_stocks_sorted:
                        stock_name = get_stock_name_by_code(stock)
                        has_today = any(news_item["is_today"] for news_item in news_list)
                        
                        expander_title = f"🔴 【{stock_name} ({stock})】 攔截到 {len(news_list)} 筆重大訊息"
                        if has_today:
                            expander_title = f"🔥 【{stock_name} ({stock})】 今日最新即時重大訊息！(共 {len(news_list)} 筆)"
                            
                        with st.expander(expander_title, expanded=True):
                            # 將重訊排序：以 date 欄位進行由新到舊排序 (新 -> 舊)
                            sorted_news_list = sorted(news_list, key=lambda x: x.get("date") if x.get("date") is not None else date.min, reverse=True)
                            for news_item in sorted_news_list:
                                if news_item["is_today"]:
                                    st.markdown(f"<div style='color:#ef4444; font-weight:bold; padding: 4px 0;'>🔥 [今日即時] {news_item['text']}</div>", unsafe_allow_html=True)
                                else:
                                    st.markdown(f"<div style='color:gray; padding: 2px 0;'>• {news_item['text']}</div>", unsafe_allow_html=True)
                
                if clean_stocks:
                    clean_displays = [f"{get_stock_name_by_code(s)} ({s})" for s in clean_stocks]
                    with st.expander(f"✅ 近一個月內無重訊個股 (共 {len(clean_stocks)} 檔)", expanded=False):
                        st.write(", ".join(clean_displays))
                        
                st.success("🎉 重訊掃描任務精準執行完成！")
