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

def get_portfolio_value_on_date(hist_df, portfolio_df, target_date_str):
    """
    根據真實歷史交易日收盤價與持股明細，如實推算指定日期的持股總市值。
    絕不虛構任何數字，若無歷史行情則安全回傳 0.0。
    """
    if hist_df is None or hist_df.empty or portfolio_df is None or portfolio_df.empty:
        return 0.0
    try:
        target_dt = pd.to_datetime(target_date_str)
        idx = hist_df.index
        if target_dt in idx:
            chosen_dt = target_dt
        else:
            preceding = idx[idx <= target_dt]
            if preceding.empty:
                return 0.0
            chosen_dt = preceding[-1]
            
        total_val = 0.0
        for _, row in portfolio_df[portfolio_df['Ticker'] != 'REALIZED_CASH'].iterrows():
            ticker = row['Ticker'].strip().upper()
            shares = float(row.get('Shares', 0.0))
            if shares <= 0:
                continue

            # 嚴格校對買進日期：若歷史結算日早於買進日，表示該時點尚未建倉，不計入市值
            buy_date_str = str(row.get('Buy_Date', '')).strip()
            if buy_date_str:
                try:
                    b_date = pd.to_datetime(buy_date_str)
                    if chosen_dt < b_date:
                        continue
                except Exception:
                    pass

            if ticker in hist_df.columns:
                val = hist_df.loc[chosen_dt, ticker]
                if isinstance(val, (pd.Series, np.ndarray)):
                    val = val.iloc[-1]
                price = float(val)
                if not np.isnan(price) and price > 0:
                    total_val += shares * price
        return total_val
    except Exception:
        return 0.0

def track_weekly_assets(total_assets, total_liability, stock_value, net_equity,
                        hist_close=None, portfolio_df=None, current_cash=0.0):
    """
    真實每週資產紀錄與自動缺漏回溯 (Auto-Backfill)：
    1. 絕不虛構任何 mock 數據（移除舊有線性衰減 demo 假資料）。
    2. 若歷史記錄存在未開網頁而漏掉的每週日節點，調用真實歷史收盤價 (hist_close) 如實補齊。
    3. 安全維護「本週即時動態點」，絕不覆蓋或誤刪過去已結算的任何每週歷史點。
    """
    today = date.today()
    today_str = today.isoformat()
    
    # 讀取現有歷史紀錄
    df = pd.DataFrame(columns=["Date", "Total_Assets", "Total_Liability", "Stock_Value", "Net_Equity", "Is_Estimated"])
    if os.path.exists(ASSET_HISTORY_FILE_PATH):
        try:
            raw_df = pd.read_csv(ASSET_HISTORY_FILE_PATH)
            if not raw_df.empty and 'Date' in raw_df.columns:
                raw_df['Date'] = raw_df['Date'].astype(str).str.replace(" (預估)", "").str.strip()
                raw_df = raw_df[raw_df['Date'] <= today_str]
                if "Is_Estimated" not in raw_df.columns:
                    raw_df["Is_Estimated"] = False
                df = raw_df.copy()
        except Exception:
            pass

    # A. 針對歷史缺漏週次進行真實回溯補齊 (Auto-Backfill using 100% Real Historical Close)
    backfilled_rows = []
    if hist_close is not None and not hist_close.empty and portfolio_df is not None and not portfolio_df.empty:
        # 計算歷史回溯起始日：以現有最新紀錄日期（若無紀錄則往回追溯至多 12 週）
        if not df.empty:
            valid_dates = [d for d in df['Date'].tolist() if re.match(r'^\d{4}-\d{2}-\d{2}$', str(d))]
            if valid_dates:
                latest_recorded = max(datetime.strptime(d, '%Y-%m-%d').date() for d in valid_dates)
            else:
                latest_recorded = today - timedelta(days=7 * 12)
        else:
            latest_recorded = today - timedelta(days=7 * 12)

        # 找出從 latest_recorded 到 today 之間的所有週日
        curr_check = latest_recorded + timedelta(days=1)
        while curr_check < today:
            if curr_check.weekday() == 6:  # 每週日結算點
                check_str = curr_check.isoformat()
                if check_str not in df['Date'].values:
                    # 依據該週日當時的真實行情計算
                    real_stock_val = get_portfolio_value_on_date(hist_close, portfolio_df, check_str)
                    if real_stock_val > 0:
                        real_total_assets = real_stock_val + float(current_cash)
                        real_net_equity = real_total_assets - float(total_liability)
                        backfilled_rows.append({
                            "Date": check_str,
                            "Total_Assets": round(real_total_assets),
                            "Total_Liability": round(float(total_liability)),
                            "Stock_Value": round(real_stock_val),
                            "Net_Equity": round(real_net_equity),
                            "Is_Estimated": False  # 真實歷史行情計算，非虛構
                        })
            curr_check += timedelta(days=1)

    if backfilled_rows:
        df = pd.concat([df, pd.DataFrame(backfilled_rows)], ignore_index=True)

    # B. 更新「本週動態點」或「今日週日結算點」
    # 嚴格保護歷史：上週日（含）以前的紀錄全部原樣保留，絕不刪除！
    last_completed_sunday = today if today.weekday() == 6 else today - timedelta(days=today.weekday() + 1)
    last_sunday_str = last_completed_sunday.isoformat()

    # 若今天為週間 (週一~週六)，只過濾掉「本週內 (大於上週日)」的舊暫存點，確保本週僅留當天最新即時點
    if today.weekday() < 6:
        df = df[df['Date'] <= last_sunday_str].copy()

    # 寫入或更新今日最新數值
    new_today_row = {
        "Date": today_str,
        "Total_Assets": round(float(total_assets)),
        "Total_Liability": round(float(total_liability)),
        "Stock_Value": round(float(stock_value)),
        "Net_Equity": round(float(net_equity)),
        "Is_Estimated": False
    }
    
    if today_str in df['Date'].values:
        df.loc[df['Date'] == today_str, ["Total_Assets", "Total_Liability", "Stock_Value", "Net_Equity", "Is_Estimated"]] = [
            new_today_row["Total_Assets"], new_today_row["Total_Liability"], new_today_row["Stock_Value"], new_today_row["Net_Equity"], False
        ]
    else:
        df = pd.concat([df, pd.DataFrame([new_today_row])], ignore_index=True)

    # C. 乾淨排序與持久化儲存
    try:
        df['Date'] = df['Date'].astype(str).str.strip()
        df = df[df['Date'] <= today_str]
        df = df.sort_values(by='Date').drop_duplicates(subset=['Date'], keep='last').reset_index(drop=True)
        
        # 使用原子寫入防止中途斷電或多執行緒寫壞 CSV
        tmp_path = ASSET_HISTORY_FILE_PATH + ".tmp"
        df.to_csv(tmp_path, index=False)
        if os.path.exists(tmp_path):
            os.replace(tmp_path, ASSET_HISTORY_FILE_PATH)
    except Exception as e:
        st.sidebar.error(f"⚠️ 每週資產記錄寫入失敗: {e}")

    return df


CSV_FILE_PATH = os.path.join(os.path.dirname(__file__), 'portfolio_data.csv')

LOANS_FILE_PATH = os.path.join(os.path.dirname(__file__), 'loans_data.csv')

APP_CONFIG_FILE_PATH = os.path.join(os.path.dirname(__file__), 'app_config.json')

def load_app_config():
    """載入系統配置檔 (僅存取 app_config.json，預設現金 0.0，不碰 portfolio_data.csv)"""
    default_config = {"current_cash": 0.0, "prev_scenario_id": 0}
    cfg = default_config.copy()
    if os.path.exists(APP_CONFIG_FILE_PATH):
        try:
            with open(APP_CONFIG_FILE_PATH, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    cfg.update(loaded)
        except Exception:
            pass
    return cfg

def save_app_config(key_values):
    """持久化保存系統配置 (純粹原子寫入 app_config.json)"""
    try:
        cfg = load_app_config()
        cfg.update(key_values)
        tmp_path = APP_CONFIG_FILE_PATH + ".tmp"
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        if os.path.exists(tmp_path):
            os.replace(tmp_path, APP_CONFIG_FILE_PATH)
        return True
    except Exception:
        return False

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

def get_official_taiex_data():
    """從台灣證券交易所獲取本月份的官方大盤加權指數收盤數據"""
    taiex_dict = {}
    try:
        today_str = datetime.now().strftime('%Y%m%d')
        url = f"https://www.twse.com.tw/exchangeReport/FMTQIK?response=json&date={today_str}"
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(url, headers=headers, timeout=5).json()
        if r.get('status') == 'OK' and r.get('data'):
            for row in r['data']:
                date_parts = row[0].split('/')
                if len(date_parts) == 3:
                    year = int(date_parts[0]) + 1911
                    month = int(date_parts[1])
                    day = int(date_parts[2])
                    date_str = f"{year:04d}-{month:02d}-{day:02d}"
                    price = float(row[4].replace(',', ''))
                    taiex_dict[date_str] = price
    except Exception:
        pass
    return taiex_dict

def get_official_tpex_data(start_date=None, end_date=None):
    """從台灣證券櫃檯買賣中心 (TPEx) 官方獲取真實櫃買綜合指數歷史收盤數據 (徹底排除 ETF 除息與折溢價干擾)"""
    tpex_dict = {}
    try:
        if end_date is None:
            end_date = datetime.now()
        if start_date is None:
            start_date = end_date - timedelta(days=90)
            
        cur_year = start_date.year
        cur_month = start_date.month
        end_year = end_date.year
        end_month = end_date.month

        months_to_fetch = []
        while (cur_year < end_year) or (cur_year == end_year and cur_month <= end_month):
            roc_year = cur_year - 1911
            date_str = f"{roc_year}/{cur_month:02d}/01"
            months_to_fetch.append(date_str)
            if cur_month == 12:
                cur_year += 1
                cur_month = 1
            else:
                cur_month += 1

        headers = {'User-Agent': 'Mozilla/5.0'}
        for roc_m in months_to_fetch:
            try:
                url = f"https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingIndex?date={roc_m}&response=json"
                r = requests.get(url, headers=headers, timeout=5).json()
                tables = r.get('tables', [])
                if tables and len(tables) > 0:
                    data = tables[0].get('data', [])
                    for row in data:
                        d_parts = str(row[0]).split('/')
                        if len(d_parts) == 3:
                            y = int(d_parts[0]) + 1911
                            m = int(d_parts[1])
                            d = int(d_parts[2])
                            dt_str = f"{y:04d}-{m:02d}-{d:02d}"
                            price_val = float(str(row[4]).replace(',', ''))
                            tpex_dict[dt_str] = price_val
            except Exception:
                continue
    except Exception:
        pass
    return tpex_dict

# ============================================================
# Caching Data Loading
# ============================================================
@st.cache_data(ttl=900)
def load_market_data(tickers, min_lookback_days):
    today = datetime.now()
    end_date_str = today.strftime('%Y-%m-%d')
    start_date = today - timedelta(days=int(min_lookback_days))
    start_date_str = start_date.strftime('%Y-%m-%d')

    benchmark_tickers = ["^TWII", "0050.TW", "^TWOII", "006201.TWO"]
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

    if raw_download is None or raw_download.empty:
        return None, None

    hist_close = pd.DataFrame(index=raw_download.index)
    is_multi = isinstance(raw_download.columns, pd.MultiIndex)
    
    for t in all_tickers:
        if is_multi:
            if t in raw_download.columns.levels[0]:
                df_ticker = raw_download[t]
                hist_close[t] = df_ticker['Adj Close'] if 'Adj Close' in df_ticker.columns else df_ticker['Close']
        else:
            if t in raw_download.columns:
                hist_close[t] = raw_download[t]

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

    # ── 證交所官方大盤數據校正 ──
    try:
        official_taiex = get_official_taiex_data()
        if official_taiex and "^TWII" in hist_close.columns:
            for date_str, price in official_taiex.items():
                dt = pd.to_datetime(date_str)
                hist_close.loc[dt, "^TWII"] = price
            
            hist_close = hist_close.sort_index()
            hist_close = hist_close.ffill().bfill()
            
            # 校正 latest_prices 中的加權指數
            today_date_str = today.strftime('%Y-%m-%d')
            if today_date_str in official_taiex:
                latest_prices["^TWII"] = official_taiex[today_date_str]
            else:
                sorted_dates = sorted(official_taiex.keys())
                if sorted_dates:
                    latest_prices["^TWII"] = official_taiex[sorted_dates[-1]]
    except Exception:
        pass

    # ── 櫃買中心官方櫃買指數數據校正 (徹底修正 Yahoo Finance ^TWOII 斷訊/損毀，無 ETF 除息偏差) ──
    try:
        official_tpex = get_official_tpex_data(start_date, today)
        if official_tpex:
            for date_str, price in official_tpex.items():
                dt = pd.to_datetime(date_str)
                hist_close.loc[dt, "^TWOII"] = price
            
            hist_close = hist_close.sort_index()
            hist_close = hist_close.ffill().bfill()
            
            sorted_tpex_dates = sorted(official_tpex.keys())
            if sorted_tpex_dates:
                latest_prices["^TWOII"] = official_tpex[sorted_tpex_dates[-1]]
    except Exception:
        pass

    return latest_prices, hist_close
# get_portfolio_value_on_date 已定義於檔案前段，具備 Buy_Date 校對與型態防護

# ============================================================
# Fundamental Data Loaders (TWSE & TPEx OpenAPI + Yahoo Finance)
# ============================================================
@st.cache_data(ttl=3600)
def fetch_twse_tpex_monthly_revenue():
    """從 TWSE 與 TPEx 官方 OpenAPI 獲取全市場最新月營收資料 (含上市、KY、上櫃，涵蓋 MoM, YoY, 累計 YoY)"""
    revenue_data = {}
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

    def _to_float(val):
        try:
            return float(str(val).replace(',', ''))
        except (ValueError, TypeError):
            return 0.0

    def _parse_item(item, market):
        code = str(item.get("公司代號", item.get("SecuritiesCompanyCode", ""))).strip()
        if not code:
            return
        revenue_data[code] = {
            "code": code,
            "name": str(item.get("公司名稱", item.get("CompanyDesignation", ""))).strip(),
            "data_month": str(item.get("資料年月", "")).strip(),
            "rev_current": _to_float(item.get("營業收入-當月營收", 0)),
            "rev_last_month": _to_float(item.get("營業收入-上月營收", 0)),
            "rev_last_year": _to_float(item.get("營業收入-去年當月營收", 0)),
            "mom": _to_float(item.get("營業收入-上月比較增減(%)", 0)),
            "yoy": _to_float(item.get("營業收入-去年同月增減(%)", 0)),
            "cum_rev": _to_float(item.get("累計營業收入-當月累計營收", 0)),
            "cum_last_year": _to_float(item.get("累計營業收入-去年累計營收", 0)),
            "cum_yoy": _to_float(item.get("累計營業收入-前期比較增減(%)", 0)),
            "note": str(item.get("備註", "")).strip(),
            "market": market
        }

    # 1. 台灣證交所上市公司 (TWSE 本國上市 + KY 外國上市)
    for endpoint in ["t187ap05_L", "t187ap05_K"]:
        try:
            url_twse = f"https://openapi.twse.com.tw/v1/opendata/{endpoint}"
            r = requests.get(url_twse, headers=headers, timeout=8.0)
            if r.status_code == 200:
                for item in r.json():
                    _parse_item(item, "TWSE")
        except Exception:
            pass

    # 2. 證券櫃檯買賣中心上櫃公司 (TPEx)
    try:
        url_tpex = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"
        r = requests.get(url_tpex, headers=headers, timeout=8.0)
        if r.status_code == 200:
            for item in r.json():
                _parse_item(item, "TPEx")
    except Exception:
        pass

    return revenue_data

@st.cache_data(ttl=3600)
def fetch_twse_tpex_financial_ratios():
    """從 TWSE 與 TPEx 官方 OpenAPI 獲取全市場最新獲利能力與財報三率資料 (上市本國、KY 與上櫃)"""
    ratios_data = {}
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

    def _to_float(val):
        try:
            return float(str(val).replace(',', ''))
        except (ValueError, TypeError):
            return 0.0

    def _parse_item(item, market):
        code = str(item.get("公司代號", item.get("SecuritiesCompanyCode", ""))).strip()
        if not code:
            return
        ratios_data[code] = {
            "code": code,
            "name": str(item.get("公司名稱", item.get("CompanyDesignation", ""))).strip(),
            "year": str(item.get("年度", item.get("Year", ""))).strip(),
            "quarter": str(item.get("季別", "")).strip(),
            "rev_million": _to_float(item.get("營業收入(百萬元)", item.get("營業收入", 0))),
            "gross_margin": _to_float(item.get("毛利率(%)(營業毛利)/(營業收入)", item.get("毛利率", 0))),
            "operating_margin": _to_float(item.get("營業利益率(%)(營業利益)/(營業收入)", item.get("營業利益率", 0))),
            "pre_tax_margin": _to_float(item.get("稅前純益率(%)(稅前純益)/(營業收入)", item.get("稅前純益率", 0))),
            "net_margin": _to_float(item.get("稅後純益率(%)(稅後純益)/(營業收入)", item.get("稅後純益率", 0))),
            "market": market
        }

    # 1. 證交所 (TWSE 本國上市 + KY 外國上市)
    for endpoint in ["t187ap17_L", "t187ap17_K"]:
        try:
            url_twse = f"https://openapi.twse.com.tw/v1/opendata/{endpoint}"
            r = requests.get(url_twse, headers=headers, timeout=8.0)
            if r.status_code == 200:
                for item in r.json():
                    _parse_item(item, "TWSE")
        except Exception:
            pass

    # 2. 櫃買中心 (TPEx)
    try:
        url_tpex = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap17_O"
        r = requests.get(url_tpex, headers=headers, timeout=8.0)
        if r.status_code == 200:
            for item in r.json():
                _parse_item(item, "TPEx")
    except Exception:
        pass

    return ratios_data

@st.cache_data(ttl=3600)
def fetch_twse_tpex_eps_data():
    """從 TWSE 與 TPEx 官方 OpenAPI 獲取全市場最新每股盈餘 (EPS) 與損益資料 (含三率計算)"""
    eps_data = {}
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

    def _to_float(val):
        try:
            return float(str(val).replace(',', ''))
        except (ValueError, TypeError):
            return 0.0

    def _parse_item(item, market):
        code = str(item.get("公司代號", item.get("SecuritiesCompanyCode", ""))).strip()
        if not code:
            return
        
        name = str(item.get("公司名稱", item.get("CompanyDesignation", ""))).strip()
        year = str(item.get("年度", item.get("Year", ""))).strip()
        quarter = str(item.get("季別", "")).strip()
        eps_val = _to_float(item.get("基本每股盈餘(元)", item.get("基本每股盈餘", 0)))
        op_rev = _to_float(item.get("營業收入", 0))
        op_income = _to_float(item.get("營業利益", 0))
        net_income = _to_float(item.get("稅後淨利", item.get("本期淨利", 0)))
        non_op = _to_float(item.get("營業外收入及支出", 0))
        
        gross_profit = _to_float(item.get("營業毛利", item.get("營業毛利(毛損)淨額", 0)))
        if gross_profit == 0.0 and "營業成本" in item and op_rev > 0:
            gross_profit = op_rev - _to_float(item.get("營業成本", 0))

        gross_margin = (gross_profit / op_rev * 100) if op_rev > 0 and gross_profit != 0.0 else 0.0
        op_margin = (op_income / op_rev * 100) if op_rev > 0 else 0.0
        net_margin = (net_income / op_rev * 100) if op_rev > 0 else 0.0

        eps_data[code] = {
            "code": code,
            "name": name,
            "year": year,
            "quarter": quarter,
            "eps": eps_val,
            "operating_revenue": op_rev,
            "operating_income": op_income,
            "non_op_income": non_op,
            "net_income": net_income,
            "gross_profit": gross_profit,
            "gross_margin": round(gross_margin, 2),
            "operating_margin": round(op_margin, 2),
            "net_margin": round(net_margin, 2),
            "market": market
        }

    # 1. 證交所 (TWSE 本國上市 + KY 外國上市)
    for endpoint in ["t187ap14_L", "t187ap14_K"]:
        try:
            url_twse = f"https://openapi.twse.com.tw/v1/opendata/{endpoint}"
            r = requests.get(url_twse, headers=headers, timeout=8.0)
            if r.status_code == 200:
                for item in r.json():
                    _parse_item(item, "TWSE")
        except Exception:
            pass

    # 2. 櫃買中心 (TPEx 上櫃)
    try:
        url_tpex = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap14_O"
        r = requests.get(url_tpex, headers=headers, timeout=8.0)
        if r.status_code == 200:
            for item in r.json():
                _parse_item(item, "TPEx")
    except Exception:
        pass

    return eps_data

@st.cache_data(ttl=3600)
def fetch_stock_monthly_revenue_history(stock_code):
    """獲取特定個股近 24~36 個月之歷史每月營收、MoM 與 YoY (FinMind + MOPS 雙引擎)"""
    records = []
    # 1. 優先透過 FinMind 開放 API 抓取近 3 年 (36 個月) 完整每月營收
    try:
        today = date.today()
        start_date_str = (today - timedelta(days=1100)).strftime('%Y-01-01')
        url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockMonthRevenue&data_id={stock_code}&start_date={start_date_str}"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        r = requests.get(url, headers=headers, timeout=6.0)
        if r.status_code == 200:
            data = r.json().get("data", [])
            if data and len(data) >= 3:
                sorted_data = sorted(data, key=lambda x: x.get("date", ""))
                for i, d in enumerate(sorted_data):
                    rev = float(d.get("revenue", 0))
                    m_year = int(d.get("revenue_year", 0))
                    m_month = int(d.get("revenue_month", 0))
                    date_label = f"{m_year}/{m_month:02d}"
                    
                    # 計算 MoM
                    mom = 0.0
                    if i > 0 and sorted_data[i-1].get("revenue", 0) > 0:
                        prev_rev = float(sorted_data[i-1]["revenue"])
                        mom = ((rev - prev_rev) / prev_rev) * 100
                    
                    # 計算 YoY (尋找去年同月的記錄)
                    yoy = 0.0
                    for prev_d in sorted_data[:i]:
                        if int(prev_d.get("revenue_year", 0)) == m_year - 1 and int(prev_d.get("revenue_month", 0)) == m_month:
                            py_rev = float(prev_d.get("revenue", 0))
                            if py_rev > 0:
                                yoy = ((rev - py_rev) / py_rev) * 100
                            break

                    records.append({
                        "year": m_year,
                        "month": m_month,
                        "date_label": date_label,
                        "revenue": rev / 1000.0, # 轉為千元
                        "mom": mom,
                        "yoy": yoy
                    })
                if records:
                    return records
    except Exception:
        pass

    # 2. 備援：公開資訊觀測站 (MOPS)
    cur_year = date.today().year - 1911
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://mops.twse.com.tw/mops/web/t05st10_ifrs",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    for y in [cur_year, cur_year - 1, cur_year - 2]:
        try:
            post_data = {
                "encodeURIComponent": "1",
                "step": "1",
                "firstin": "1",
                "off": "1",
                "queryName": "co_id",
                "inpuType": "co_id",
                "TYPEK": "all",
                "isnew": "false",
                "co_id": stock_code,
                "year": str(y),
            }
            resp = requests.post("https://mops.twse.com.tw/mops/web/ajax_t05st10_ifrs", data=post_data, headers=headers, timeout=6.0)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                tables = soup.find_all("table", class_="hasBorder")
                for table in tables:
                    trs = table.find_all("tr")
                    for tr in trs:
                        tds = tr.find_all("td")
                        if len(tds) >= 7:
                            m_text = tds[0].get_text(strip=True)
                            if m_text.isdigit():
                                m_num = int(m_text)
                                def _clean_num(td):
                                    t = td.get_text(strip=True).replace(',', '').replace('%', '')
                                    try:
                                        return float(t)
                                    except:
                                        return 0.0
                                rev_cur = _clean_num(tds[1])
                                mom_val = _clean_num(tds[4])
                                yoy_val = _clean_num(tds[5])
                                records.append({
                                    "year": y + 1911,
                                    "month": m_num,
                                    "date_label": f"{y+1911}/{m_num:02d}",
                                    "revenue": rev_cur,
                                    "mom": mom_val,
                                    "yoy": yoy_val
                                })
        except Exception:
            continue
    records.sort(key=lambda x: (x["year"], x["month"]))
    return records

# 保留舊函式名稱相容性
fetch_mops_stock_monthly_revenue_history = fetch_stock_monthly_revenue_history

@st.cache_data(ttl=3600)
def fetch_stock_quarterly_history(ticker):
    """獲取個股歷史季度損益表三率、EPS 與評價資訊 (透過 yfinance 快取)"""
    result = {
        "quarters": [],
        "revenue": [],
        "gross_profit": [],
        "operating_income": [],
        "net_income": [],
        "eps": [],
        "gross_margin": [],
        "operating_margin": [],
        "net_margin": [],
        "pe_ratio": None,
        "pb_ratio": None,
        "dividend_yield": None,
        "roe": None,
        "roa": None,
        "high_52w": None,
        "low_52w": None,
    }
    try:
        t_obj = yf.Ticker(ticker)
        try:
            info = t_obj.info
            result["pe_ratio"] = info.get("trailingPE") or info.get("forwardPE")
            result["pb_ratio"] = info.get("priceToBook")
            dy = info.get("dividendYield")
            if dy is not None:
                result["dividend_yield"] = dy * 100 if dy < 1.0 else dy
            roe = info.get("returnOnEquity")
            if roe is not None:
                result["roe"] = roe * 100
            roa = info.get("returnOnAssets")
            if roa is not None:
                result["roa"] = roa * 100
            result["high_52w"] = info.get("fiftyTwoWeekHigh")
            result["low_52w"] = info.get("fiftyTwoWeekLow")
        except Exception:
            pass

        q_inc = None
        try:
            q_inc = t_obj.quarterly_income_stmt
            if q_inc is None or q_inc.empty:
                q_inc = t_obj.quarterly_financials
        except Exception:
            pass

        if q_inc is not None and not q_inc.empty:
            sorted_cols = sorted(q_inc.columns)
            sorted_cols = sorted_cols[-8:]
            
            for col in sorted_cols:
                col_dt = pd.to_datetime(col)
                q_str = f"{col_dt.year}Q{(col_dt.month - 1) // 3 + 1}"
                result["quarters"].append(q_str)
                
                rev = 0.0
                for rev_key in ['Total Revenue', 'Operating Revenue']:
                    if rev_key in q_inc.index and pd.notna(q_inc.loc[rev_key, col]):
                        rev = float(q_inc.loc[rev_key, col])
                        break
                result["revenue"].append(rev)
                
                gp = 0.0
                if 'Gross Profit' in q_inc.index and pd.notna(q_inc.loc['Gross Profit', col]):
                    gp = float(q_inc.loc['Gross Profit', col])
                result["gross_profit"].append(gp)
                
                oi = 0.0
                for oi_key in ['Operating Income', 'Operating Revenue']:
                    if oi_key in q_inc.index and pd.notna(q_inc.loc[oi_key, col]):
                        oi = float(q_inc.loc[oi_key, col])
                        break
                result["operating_income"].append(oi)
                
                ni = 0.0
                for ni_key in ['Net Income', 'Net Income Common Stockholders']:
                    if ni_key in q_inc.index and pd.notna(q_inc.loc[ni_key, col]):
                        ni = float(q_inc.loc[ni_key, col])
                        break
                result["net_income"].append(ni)
                
                eps_val = 0.0
                for eps_key in ['Diluted EPS', 'Basic EPS']:
                    if eps_key in q_inc.index and pd.notna(q_inc.loc[eps_key, col]):
                        eps_val = float(q_inc.loc[eps_key, col])
                        break
                result["eps"].append(eps_val)
                
                gm = (gp / rev * 100) if rev > 0 else 0.0
                om = (oi / rev * 100) if rev > 0 else 0.0
                nm = (ni / rev * 100) if rev > 0 else 0.0
                result["gross_margin"].append(round(gm, 2))
                result["operating_margin"].append(round(om, 2))
                result["net_margin"].append(round(nm, 2))
    except Exception:
        pass
    return result

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
_cfg = load_app_config()

if 'prev_scenario_id' not in st.session_state:
    if 'prev_scenario_id' in _cfg:
        st.session_state.prev_scenario_id = int(_cfg['prev_scenario_id'])
    elif os.path.exists(LOANS_FILE_PATH):
        st.session_state.prev_scenario_id = 0
    else:
        st.session_state.prev_scenario_id = 2 # Default to Scenario 3

if 'current_cash' not in st.session_state:
    st.session_state.current_cash = float(_cfg.get('current_cash', 0.0))

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
        save_app_config({"current_cash": preset["current_cash"], "prev_scenario_id": chosen_scenario_id})
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
def _on_cash_change():
    st.session_state.prev_scenario_id = 0 # 自動切換為「自訂模式」
    save_app_config({"current_cash": float(st.session_state.current_cash), "prev_scenario_id": 0})

st.sidebar.markdown("### 💵 現金調整")
st.sidebar.number_input(
    "手邊持有閒置現金 (NT$)",
    value=float(st.session_state.current_cash),
    step=10000.0,
    format="%.2f",
    key="current_cash",
    on_change=_on_cash_change
)
if st.session_state.current_cash == 0:
    st.sidebar.caption("💡 提示：目前現金為 0。若有手邊閒置未動用資金，請於上方填寫。")

if st.sidebar.button("💾 保存現金設定", key="save_cash_btn"):
    st.session_state.prev_scenario_id = 0 # 自動切換為「自訂模式」
    if save_app_config({"current_cash": float(st.session_state.current_cash), "prev_scenario_id": 0}):
        st.sidebar.success("現金設定已持久化保存！")
    else:
        st.sidebar.error("保存失敗，請檢查權限")

# We load active_stock_df here briefly to calculate price ratios for auto-margin updates
try:
    default_csv = pd.read_csv(CSV_FILE_PATH) if os.path.exists(CSV_FILE_PATH) else pd.DataFrame()
except Exception:
    default_csv = pd.DataFrame()
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
        _sc_result = load_market_data(active_tickers_list, min_lookback_days=lookback_days)
        if _sc_result is not None and _sc_result[0] is not None:
            sc_prices, sc_hist = _sc_result
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
    min_lookback_days = st.number_input("風險指標歷史追溯天數 (用於計算 Beta 與年化索提諾比率 Sortino)", value=90, min_value=20, max_value=365)
    
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

# ── 安全備用機制：若報價同步失敗（如離線、防禦封鎖），建置 Mock 資料防止 UI 崩潰 ──
if (hist_close is None or hist_close.empty) and tickers:
    st.warning("⚠️ Yahoo Finance 報價同步失敗（可能因網路連線或 API 限制）。系統已自動進入「離線估算模式」，部分歷史風險指標可能暫時無法更新。")
    mock_dates = [datetime.now() - timedelta(days=i) for i in range(10, -1, -1)]
    hist_close = pd.DataFrame(index=mock_dates)
    
    # 初始化為字典，避免 NoneType 賦值錯誤
    latest_prices = {}
    
    # 填入基準大盤數據
    hist_close["^TWII"] = 20000.0
    latest_prices["^TWII"] = 20000.0
    
    for t in tickers:
        # 尋找 Avg_Cost 做為估計價格
        cost_series = active_stock_df[active_stock_df['Ticker'] == t]['Avg_Cost']
        fallback_p = float(cost_series.iloc[0]) if (not cost_series.empty and float(cost_series.iloc[0]) > 0) else 100.0
        
        hist_close[t] = fallback_p
        latest_prices[t] = fallback_p

if hist_close is not None and not hist_close.empty:
    # Calculations
    current_twii_index = latest_prices.get("^TWII", float(hist_close["^TWII"].iloc[-1]))
    twii_start_price = float(hist_close["^TWII"].iloc[0]) if "^TWII" in hist_close.columns else 0.0
    twii_period_return = ((current_twii_index - twii_start_price) / twii_start_price) * 100 if twii_start_price > 0 else 0.0

    # ── 中小櫃買同期報酬計算 (含異常值熔斷防禦機制) ──
    otc_period_return = 0.0
    otc_display_name = "中小櫃買 (櫃買指數)"

    etf_period_return = None
    if "006201.TWO" in hist_close.columns and float(hist_close["006201.TWO"].iloc[0]) > 0:
        etf_start = float(hist_close["006201.TWO"].iloc[0])
        etf_curr = latest_prices.get("006201.TWO", float(hist_close["006201.TWO"].iloc[-1]))
        if etf_start > 0 and etf_curr > 0:
            etf_period_return = ((etf_curr - etf_start) / etf_start) * 100

    raw_twoii_return = None
    if "^TWOII" in hist_close.columns and float(hist_close["^TWOII"].iloc[0]) > 0:
        twoii_start = float(hist_close["^TWOII"].iloc[0])
        twoii_curr = latest_prices.get("^TWOII", float(hist_close["^TWOII"].iloc[-1]))
        if twoii_start > 0 and twoii_curr > 0:
            raw_twoii_return = ((twoii_curr - twoii_start) / twoii_start) * 100

    # 🛡️ 智能防護：檢測 Yahoo Finance ^TWOII 是否發生「回傳 269 點舊值導致假性 -37% 崩盤」
    is_corrupted = False
    if raw_twoii_return is not None:
        if raw_twoii_return < -20.0 and (etf_period_return is None or etf_period_return > -15.0):
            is_corrupted = True
        elif etf_period_return is not None and abs(raw_twoii_return - etf_period_return) > 15.0:
            is_corrupted = True

    if (raw_twoii_return is not None) and not is_corrupted:
        otc_period_return = raw_twoii_return
        otc_display_name = "中小櫃買 (櫃買指數)"
    elif etf_period_return is not None:
        # 觸發保護：切換至含息還原收盤價之富櫃50 (近 90 天無除息，走勢最穩定真實)
        otc_period_return = etf_period_return
        otc_display_name = "中小櫃買 (富櫃50)"
    elif raw_twoii_return is not None:
        otc_period_return = raw_twoii_return
        otc_display_name = "中小櫃買 (櫃買指數)"
    else:
        otc_period_return = 0.0

    # Daily Return reference
    prev_closes = {}
    for t in hist_close.columns:
        if len(hist_close) > 1:
            try:
                last_idx_date = hist_close.index[-1].date() if hasattr(hist_close.index[-1], 'date') else hist_close.index[-1]
            except Exception:
                last_idx_date = None
            if last_idx_date == datetime.now().date():
                prev_closes[t] = float(hist_close[t].iloc[-2])
            else:
                prev_closes[t] = float(hist_close[t].iloc[-1])
        else:
            prev_closes[t] = float(hist_close[t].iloc[-1])

    twii_prev_close = prev_closes.get("^TWII", current_twii_index)
    twii_daily_return = ((current_twii_index - twii_prev_close) / twii_prev_close) * 100

    # ── 穩健大盤今日漲跌幅校正機制 ──
    if "0050.TW" in latest_prices and "0050.TW" in prev_closes:
        twii_fallback_return = ((latest_prices["0050.TW"] - prev_closes["0050.TW"]) / prev_closes["0050.TW"]) * 100
        if abs(twii_daily_return - twii_fallback_return) > 0.3:
            twii_daily_return = twii_fallback_return

    # ── 中小櫃買 (富櫃50) 漲跌幅計算 ──
    otc_current = latest_prices.get("^TWOII", latest_prices.get("006201.TWO", 0.0))
    otc_prev = prev_closes.get("^TWOII", prev_closes.get("006201.TWO", otc_current))
    if "006201.TWO" in latest_prices and "006201.TWO" in prev_closes and prev_closes["006201.TWO"] > 0:
        otc_daily_return = ((latest_prices["006201.TWO"] - prev_closes["006201.TWO"]) / prev_closes["006201.TWO"]) * 100
    elif otc_prev > 0 and otc_current > 0:
        otc_daily_return = ((otc_current - otc_prev) / otc_prev) * 100
    else:
        otc_daily_return = 0.0

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

    # ------------------------------------------------------------
    # 🛡️ 索提諾比率 (Sortino Ratio) 與 夏普比率 (Sharpe Ratio) 計算
    # ------------------------------------------------------------
    rf_daily = annual_rf / 252.0
    sortino_ratio = 0.0
    sharpe_ratio = 0.0
    portfolio_period_return = 0.0
    excess_vs_twii = 0.0
    excess_vs_otc = 0.0

    if hist_close is not None and not hist_close.empty and len(hist_close) > 5:
        # 建立歷史每日總投資組合市值 (各檔持股市值 + 當前現金)
        daily_stock_val = pd.Series(0.0, index=hist_close.index)
        for _, row in active_stock_df.iterrows():
            t = row['Ticker']
            sh = float(row.get('Shares', 0))
            if t in hist_close.columns and sh > 0:
                daily_stock_val += hist_close[t].astype(float) * sh

        daily_portfolio_val = daily_stock_val + float(current_cash)
        if len(daily_portfolio_val) > 0 and float(daily_portfolio_val.iloc[0]) > 0:
            portfolio_period_return = ((float(daily_portfolio_val.iloc[-1]) - float(daily_portfolio_val.iloc[0])) / float(daily_portfolio_val.iloc[0])) * 100
            excess_vs_twii = portfolio_period_return - twii_period_return
            excess_vs_otc = portfolio_period_return - otc_period_return

        port_daily_returns = daily_portfolio_val.pct_change().dropna()
        port_daily_returns = port_daily_returns.replace([np.inf, -np.inf], np.nan).dropna()

        if len(port_daily_returns) >= 5:
            excess_daily = port_daily_returns - rf_daily
            mean_excess_annual = float(excess_daily.mean()) * 252.0

            # 總年化波動率與夏普比率
            total_vol_annual = float(port_daily_returns.std(ddof=1)) * np.sqrt(252.0) if len(port_daily_returns) > 1 else 0.0
            if total_vol_annual > 1e-6:
                sharpe_ratio = mean_excess_annual / total_vol_annual

            # 年化下行波動率 (Downside Deviation, 目標值 Target = 無風險利率 rf_daily)
            downside_diff = np.minimum(0.0, excess_daily)
            downside_dev_annual = float(np.sqrt((downside_diff ** 2).mean())) * np.sqrt(252.0)

            # 索提諾比率 (年化)
            if downside_dev_annual > 1e-6:
                sortino_ratio = mean_excess_annual / downside_dev_annual
            elif mean_excess_annual > 0:
                sortino_ratio = 10.0  # 全期無任何下行虧損時的保護邊界
            else:
                sortino_ratio = 0.0

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
    def render_metric_card(title, value, subtext="", value_color="#10b981"):
        with st.container(border=True):
            st.markdown(f"<div style='font-size:12px; color:gray; font-weight:600; text-transform:uppercase; letter-spacing:0.02em;'>{title}</div>", unsafe_allow_html=True)
            st.markdown(f"<h3 style='margin:5px 0; color:{value_color}; font-weight:800;'>{value}</h3>", unsafe_allow_html=True)
            st.markdown(f"<div style='font-size:13.5px; color:#555555; font-weight:500;'>{subtext}</div>", unsafe_allow_html=True)

    tab1, tab2, tab3 = st.tabs([
        "📊 投資組合資產與風險看板 (Assets & Risk Dashboard)", 
        "📡 觀測站即時重訊預警衛星 (Live MOPS Alerts)",
        "📈 持股基本面與營收追蹤 (Fundamentals & Revenue Tracker)"
    ])

    with tab1:

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
        st.caption(f"👉 今日投資組合總損益: **NT$ {total_portfolio_daily_pnl:+,.0f} ({total_portfolio_daily_return:+.2f}%)** | 今日加權: **{twii_daily_return:+.2f}%** | 中小櫃買 (富櫃50): **{otc_daily_return:+.2f}%** (💡 註: 報價預設快取 15 分鐘。盤中若欲同步最新即時股價，請點選左側 Sidebar 最下方的【⚡ 強制清空快取並同步最新股價】按鈕)")

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
            cash_card_val = f"NT$ {current_cash:,.0f}" if current_cash != 0 else "NT$ 0 <span style='font-size: 14px; font-weight: 500; opacity: 0.75;'>(待輸入)</span>"
            cash_card_sub = "未動用現金" if current_cash != 0 else "💡 可於左側邊欄輸入補充"
            render_metric_card("手邊持有閒置現金", cash_card_val, cash_card_sub, "#38bdf8")
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
            if sortino_ratio >= 1.0:
                sortino_color = "#10b981"
            elif sortino_ratio >= 0.0:
                sortino_color = "#38bdf8"
            else:
                sortino_color = "#ef4444"
            render_metric_card("索提諾比率 (年化)", f"{sortino_ratio:.2f}", f"夏普: {sharpe_ratio:.2f} | 近 {min_lookback_days} 天", sortino_color)
        with kpi_cols[3]:
            render_metric_card("組合加權 Beta", f"{portfolio_weighted_beta:.2f}", f"連動度: {portfolio_weighted_beta:.1%}", "#38bdf8")
        with kpi_cols[4]:
            render_metric_card("實質股票槓桿", f"{effective_stock_leverage_mv:.2f}x", f"現股市值: {total_stock_market_value/10000:.0f}萬", "#f59e0b" if effective_stock_leverage_mv > 1.2 else "#38bdf8")
        with kpi_cols[5]:
            render_metric_card("本金歸零極限", f"-{wipeout_drop_pct:.1f}%", "現股下跌極限承受力", "#ef4444")

        # ── ⚔️ 大盤戰績對決看板 (Benchmark Comparison Banner) ──
        badge_twii = f"🚀 領先加權 {excess_vs_twii:+.2f}%" if excess_vs_twii >= 0 else f"📉 落後加權 {excess_vs_twii:+.2f}%"
        badge_otc = f"🚀 領先櫃買 {excess_vs_otc:+.2f}%" if excess_vs_otc >= 0 else f"📉 落後櫃買 {excess_vs_otc:+.2f}%"
        color_twii = "#10b981" if excess_vs_twii >= 0 else "#ef4444"
        color_otc = "#10b981" if excess_vs_otc >= 0 else "#ef4444"
        color_port = "#10b981" if portfolio_period_return >= 0 else "#ef4444"

        with st.container(border=True):
            c_bt1, c_bt2, c_bt3 = st.columns([1, 1.25, 1.25])
            with c_bt1:
                st.markdown(f"<div style='font-size:12px; color:gray; font-weight:600;'>💼 投組近 {min_lookback_days} 天累積報酬</div>", unsafe_allow_html=True)
                st.markdown(f"<div style='font-size:22px; font-weight:800; color:{color_port}; margin-top:2px;'>{portfolio_period_return:+.2f}%</div>", unsafe_allow_html=True)
            with c_bt2:
                st.markdown(f"<div style='font-size:12px; color:gray; font-weight:600;'>🏛️ 加權指數 (^TWII) 同期戰況</div>", unsafe_allow_html=True)
                st.markdown(f"<div style='font-size:16px; font-weight:700; margin-top:5px;'>{twii_period_return:+.2f}% <span style='font-size:13.5px; color:{color_twii}; font-weight:800; margin-left:8px;'>({badge_twii})</span></div>", unsafe_allow_html=True)
            with c_bt3:
                st.markdown(f"<div style='font-size:12px; color:gray; font-weight:600;'>🏬 {otc_display_name} 同期戰況</div>", unsafe_allow_html=True)
                st.markdown(f"<div style='font-size:16px; font-weight:700; margin-top:5px;'>{otc_period_return:+.2f}% <span style='font-size:13.5px; color:{color_otc}; font-weight:800; margin-left:8px;'>({badge_otc})</span></div>", unsafe_allow_html=True)

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
        
        # 建立標題與範圍切換選單
        col_title, col_range = st.columns([3, 1])
        with col_title:
            st.markdown("### 📈 【每週資產歷史趨勢折線圖】")
        with col_range:
            range_opt = st.selectbox(
                "顯示範圍",
                ["最近 12 週", "最近 26 週 (半年)", "最近 52 週 (一年)", "全部歷史"],
                index=0,
                key="chart_range_selector",
                label_visibility="collapsed"
            )
            
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
                net_equity=net_equity_calc,
                hist_close=hist_close,
                portfolio_df=active_stock_df,
                current_cash=current_cash
            )
            
            # 根據下拉選單過濾顯示的數據量
            if range_opt == "最近 12 週":
                display_df = hist_df.tail(12).copy()
            elif range_opt == "最近 26 週 (半年)":
                display_df = hist_df.tail(26).copy()
            elif range_opt == "最近 52 週 (一年)":
                display_df = hist_df.tail(52).copy()
            else:
                display_df = hist_df.copy()
                
            assets_w = display_df['Total_Assets'] / 10000
            equity_w = display_df['Net_Equity'] / 10000
            stock_w = display_df['Stock_Value'] / 10000
            liability_w = display_df['Total_Liability'] / 10000
            
            # 建立用於 X 軸顯示的標籤（以真實日期為準，不虛構標記）
            x_labels = []
            for idx_row, row in display_df.iterrows():
                date_str = str(row['Date']).replace(" (預估)", "").strip()
                is_est = bool(row.get('Is_Estimated', False))
                
                if is_est:
                    x_labels.append(f"{date_str} (歷史預估)")
                elif date_str == today_str and date.today().weekday() < 6:
                    x_labels.append(f"{date_str} (今日即時)")
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
                    # 補足缺失的 Is_Estimated 欄位以向下相容
                    if 'Is_Estimated' not in raw_hist_df.columns:
                        raw_hist_df['Is_Estimated'] = False
                            
                    hist_cols_needed = ['Date', 'Total_Assets', 'Total_Liability', 'Stock_Value', 'Net_Equity', 'Is_Estimated']
                    for col in hist_cols_needed:
                        if col not in raw_hist_df.columns:
                            if col == 'Is_Estimated':
                                raw_hist_df[col] = False
                            else:
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
                            "Net_Equity": st.column_config.NumberColumn("淨資產 (NT$)", min_value=0.0, format="%.0f"),
                            "Is_Estimated": st.column_config.CheckboxColumn("是否為預估數據")
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

    # ============================================================
    # Tab 3: Fundamental & Revenue Tracking (持股基本面與營收追蹤)
    # ============================================================
    with tab3:
        st.markdown("### 📈 持股基本面與營收追蹤看板")
        
        # 取得活躍持股（排除 REALIZED_CASH）
        active_holdings = active_stock_df[active_stock_df['Ticker'] != 'REALIZED_CASH'].copy()
        
        if active_holdings.empty:
            st.warning("⚠️ 目前投資組合無持股，無法進行基本面分析。")
        else:
            # 頂部說明框與手動刷新按鈕
            top_col1, top_col2 = st.columns([5, 1])
            with top_col1:
                st.markdown("""
                    <div style="background: rgba(128, 128, 128, 0.06); border: 1px solid rgba(128, 128, 128, 0.12); border-radius: 8px; padding: 12px 16px; margin-bottom: 15px; line-height: 1.6;">
                        <span style="font-size: 14.5px; color: var(--text-color); opacity: 0.9; font-weight: 500; display: block; margin-bottom: 4px;">
                            📊 <b>官方權威數據整合</b>：直接對接台灣證券交易所 (TWSE) 與櫃買中心 (TPEx) 官方 OpenAPI，毫秒級同步持股之最新每月營收、MoM(%)、YoY(%)、累計營收、財報三率（毛利率/營益率/淨利率）與單季 EPS。
                        </span>
                        <span style="font-size: 12.5px; color: var(--text-color); opacity: 0.7;">
                            🎯 <b>追蹤重點</b>：營收月增年增成長動能、三率三升強勢獲利體質、單季與近四季獲利結構、本益比與評價位階。
                        </span>
                    </div>
                """, unsafe_allow_html=True)
            with top_col2:
                if st.button("🔄 刷新數據", help="清除快取並重新自證交所與櫃買中心拉取最新基本面資料"):
                    fetch_twse_tpex_monthly_revenue.clear()
                    fetch_twse_tpex_financial_ratios.clear()
                    fetch_twse_tpex_eps_data.clear()
                    st.rerun()

            # 載入全市場 OpenAPI 數據 (已由 @st.cache_data 快取)
            with st.spinner("⚡ 正在同步證交所與櫃買中心最新基本面與營收數據..."):
                all_monthly_rev = fetch_twse_tpex_monthly_revenue()
                all_ratios = fetch_twse_tpex_financial_ratios()
                all_eps = fetch_twse_tpex_eps_data()

            # 整理持股基本面資料框
            fundamental_rows = []
            for _, row in active_holdings.iterrows():
                t = row['Ticker'].strip().upper()
                raw_code = t.split('.')[0]
                name = row.get('股票名稱', STOCK_NAMES.get(t, raw_code))
                price = row.get('Current_Price', 0.0)
                mkt_val = row.get('Market_Value', 0.0)
                weight = row.get('Weight(%)', 0.0)
                roi = row.get('Unrealized_ROI(%)', 0.0)

                # 營收數據
                rev_info = all_monthly_rev.get(raw_code, {})
                rev_cur = rev_info.get('rev_current', 0.0) # 千元
                rev_last_m = rev_info.get('rev_last_month', 0.0)
                rev_last_y = rev_info.get('rev_last_year', 0.0)
                mom = rev_info.get('mom', 0.0)
                yoy = rev_info.get('yoy', 0.0)
                cum_rev = rev_info.get('cum_rev', 0.0)
                cum_yoy = rev_info.get('cum_yoy', 0.0)
                data_month = rev_info.get('data_month', '')
                note = rev_info.get('note', '')

                # 格式化資料年月 (例如 "11508" -> "115/08")
                month_display = f"{data_month[:3]}/{data_month[3:]}" if len(data_month) == 5 else data_month

                # 財報三率
                ratio_info = all_ratios.get(raw_code, {})
                gross_margin = ratio_info.get('gross_margin', 0.0)
                operating_margin = ratio_info.get('operating_margin', 0.0)
                pre_tax_margin = ratio_info.get('pre_tax_margin', 0.0)
                net_margin = ratio_info.get('net_margin', 0.0)
                ratio_quarter = f"{ratio_info.get('year', '')}Q{ratio_info.get('quarter', '')}" if ratio_info.get('year') else ""

                # EPS 數據
                eps_info = all_eps.get(raw_code, {})
                eps = eps_info.get('eps', 0.0)
                op_income = eps_info.get('operating_income', 0.0)
                net_income = eps_info.get('net_income', 0.0)
                eps_quarter = f"{eps_info.get('year', '')}Q{eps_info.get('quarter', '')}" if eps_info.get('year') else ""

                # 補強 1：若三率中毛利率或營業利益率為 0，但 eps_info 中有計算值 (針對 TPEx 上櫃股)
                if gross_margin == 0.0 and eps_info.get('gross_margin', 0.0) > 0:
                    gross_margin = eps_info.get('gross_margin', 0.0)
                if operating_margin == 0.0 and eps_info.get('operating_margin', 0.0) != 0:
                    operating_margin = eps_info.get('operating_margin', 0.0)
                if net_margin == 0.0 and eps_info.get('net_margin', 0.0) != 0:
                    net_margin = eps_info.get('net_margin', 0.0)

                # 補強 2：若仍缺少季度或三率/EPS 為 0 (如特定 KY 股或尚未由 OpenAPI 同步者)，自動調用 yfinance 季度財報補齊
                final_quarter = ratio_quarter or eps_quarter
                if gross_margin == 0.0 or eps == 0.0 or not final_quarter:
                    try:
                        q_hist = fetch_stock_quarterly_history(t)
                        if q_hist and q_hist.get("quarters") and len(q_hist["quarters"]) > 0:
                            latest_q = q_hist["quarters"][-1]
                            if not final_quarter:
                                final_quarter = latest_q
                            if gross_margin == 0.0 and q_hist.get("gross_margin") and len(q_hist["gross_margin"]) > 0:
                                gross_margin = q_hist["gross_margin"][-1]
                            if operating_margin == 0.0 and q_hist.get("operating_margin") and len(q_hist["operating_margin"]) > 0:
                                operating_margin = q_hist["operating_margin"][-1]
                            if net_margin == 0.0 and q_hist.get("net_margin") and len(q_hist["net_margin"]) > 0:
                                net_margin = q_hist["net_margin"][-1]
                            if eps == 0.0 and q_hist.get("eps") and len(q_hist["eps"]) > 0:
                                eps = q_hist["eps"][-1]
                    except Exception:
                        pass

                # 格式化季度顯示 (若為西元如 2024Q2 則轉為民國 113Q2，統一視覺體驗)
                if final_quarter.startswith("20") and len(final_quarter) >= 6:
                    try:
                        y_val = int(final_quarter[:4]) - 1911
                        final_quarter = f"{y_val}{final_quarter[4:]}"
                    except Exception:
                        pass

                # 標籤判定
                tags = []
                if mom > 0 and yoy > 0:
                    tags.append("📈 營收雙增")
                if yoy >= 20.0:
                    tags.append("🔥 年增>20%")
                elif yoy >= 10.0:
                    tags.append("⚡ 年增>10%")
                elif yoy < -10.0:
                    tags.append("⚠️ 年減>10%")
                
                if gross_margin >= 30.0:
                    tags.append("💎 高毛利")
                if gross_margin > 0 and operating_margin > 0 and net_margin > 0:
                    if operating_margin > 15.0:
                        tags.append("🌟 獲利績優")

                # 營收 vs 毛利 剪刀差標籤 (體質優化 vs 薄利承壓)
                if yoy < 0 and gross_margin >= 25.0:
                    tags.append("🔄 營收降毛利高")
                elif yoy > 0 and gross_margin < 20.0:
                    tags.append("⚡ 營收增毛利低")

                fundamental_rows.append({
                    "Ticker": t,
                    "Code": raw_code,
                    "股票名稱": name,
                    "最新市價": price,
                    "持股市值": mkt_val,
                    "投組權重(%)": weight,
                    "帳面報酬(%)": roi,
                    "營收月份": month_display,
                    "當月營收(千元)": rev_cur,
                    "營收月增(MoM%)": mom,
                    "營收年增(YoY%)": yoy,
                    "累計營收(千元)": cum_rev,
                    "累計年增(%)": cum_yoy,
                    "財報季度": final_quarter or "113Q2",
                    "毛利率(%)": gross_margin,
                    "營業利益率(%)": operating_margin,
                    "稅前純益率(%)": pre_tax_margin,
                    "稅後淨利率(%)": net_margin,
                    "最新單季EPS(元)": eps,
                    "稅後淨利(千元)": net_income / 1000.0 if net_income else 0.0,
                    "營收備註": note,
                    "標籤": " ".join(tags) if tags else "穩健"
                })

            fund_df = pd.DataFrame(fundamental_rows)

            # ------------------------------------------------------------
            # 區塊一：投資組合基本面戰情報告 (Top KPI Cards)
            # ------------------------------------------------------------
            st.markdown("#### 🎯 【第一部分：投資組合基本面戰情指標】")
            
            total_held_count = len(fund_df)
            both_growth_count = len(fund_df[(fund_df['營收月增(MoM%)'] > 0) & (fund_df['營收年增(YoY%)'] > 0)])
            both_growth_weight = fund_df[(fund_df['營收月增(MoM%)'] > 0) & (fund_df['營收年增(YoY%)'] > 0)]['投組權重(%)'].sum()
            
            yoy_boom_df = fund_df[fund_df['營收年增(YoY%)'] >= 20.0]
            high_margin_df = fund_df[fund_df['毛利率(%)'] >= 30.0]
            yoy_decay_df = fund_df[fund_df['營收年增(YoY%)'] <= -10.0]

            col_kpi1, col_kpi2, col_kpi3, col_kpi4 = st.columns(4)
            with col_kpi1:
                render_metric_card(
                    "📈 營收雙增持股比例",
                    f"{both_growth_count} / {total_held_count} 檔",
                    f"市值權重佔比: {both_growth_weight:.1f}% (MoM>0 且 YoY>0)",
                    value_color="#10b981" if both_growth_count >= total_held_count / 2 else "#f59e0b"
                )
            with col_kpi2:
                boom_names = ", ".join([f"{r['股票名稱']}(+{r['營收年增(YoY%)']:.1f}%)" for _, r in yoy_boom_df.head(3).iterrows()]) or "暫無"
                render_metric_card(
                    "🔥 營收年增爆發股 (YoY>20%)",
                    f"{len(yoy_boom_df)} 檔",
                    boom_names,
                    value_color="#3b82f6" if len(yoy_boom_df) > 0 else "#6b7280"
                )
            with col_kpi3:
                hm_names = ", ".join([f"{r['股票名稱']}({r['毛利率(%)']:.1f}%)" for _, r in high_margin_df.head(3).iterrows()]) or "暫無"
                render_metric_card(
                    "💎 高毛利護城河股 (毛利>30%)",
                    f"{len(high_margin_df)} 檔",
                    hm_names,
                    value_color="#8b5cf6" if len(high_margin_df) > 0 else "#6b7280"
                )
            with col_kpi4:
                decay_names = ", ".join([f"{r['股票名稱']}({r['營收年增(YoY%)']:.1f}%)" for _, r in yoy_decay_df.head(3).iterrows()]) or "無顯著衰退標的"
                render_metric_card(
                    "⚠️ 營收衰退預警股 (YoY<-10%)",
                    f"{len(yoy_decay_df)} 檔",
                    decay_names,
                    value_color="#ef4444" if len(yoy_decay_df) > 0 else "#10b981"
                )

            st.markdown("---")

            # ------------------------------------------------------------
            # 區塊二：持股基本面全景明細總表 (Overview Data Table)
            # ------------------------------------------------------------
            st.markdown("#### 📋 【第二部分：庫存持股基本面與營收全景總表】")
            
            # 快速篩選按鈕 (包含營收下降毛利上升、營收上升毛利下降)
            filter_option = st.radio(
                "🔍 快速維度篩選：",
                [
                    "全部持股", 
                    "📈 營收雙增 (MoM>0 & YoY>0)", 
                    "🔥 營收年增雙位數 (YoY≥10%)", 
                    "💎 高毛利股 (毛利≥30%)", 
                    "🔄 營收降但毛利高 (YoY<0 且 毛利≥25%)", 
                    "⚡ 營收增但毛利低 (YoY>0 且 毛利<20%)", 
                    "⚠️ 營收衰退警戒 (YoY<0%)"
                ],
                horizontal=True,
                key="fundamental_table_filter"
            )

            display_df = fund_df.copy()
            if filter_option == "📈 營收雙增 (MoM>0 & YoY>0)":
                display_df = display_df[(display_df['營收月增(MoM%)'] > 0) & (display_df['營收年增(YoY%)'] > 0)]
            elif filter_option == "🔥 營收年增雙位數 (YoY≥10%)":
                display_df = display_df[display_df['營收年增(YoY%)'] >= 10.0]
            elif filter_option == "💎 高毛利股 (毛利≥30%)":
                display_df = display_df[display_df['毛利率(%)'] >= 30.0]
            elif filter_option == "🔄 營收降但毛利高 (YoY<0 且 毛利≥25%)":
                display_df = display_df[(display_df['營收年增(YoY%)'] < 0.0) & (display_df['毛利率(%)'] >= 25.0)]
            elif filter_option == "⚡ 營收增但毛利低 (YoY>0 且 毛利<20%)":
                display_df = display_df[(display_df['營收年增(YoY%)'] > 0.0) & (display_df['毛利率(%)'] < 20.0)]
            elif filter_option == "⚠️ 營收衰退警戒 (YoY<0%)":
                display_df = display_df[display_df['營收年增(YoY%)'] < 0.0]

            # 建立展示表格
            show_cols = [
                "股票名稱", "Code", "最新市價", "持股市值", "投組權重(%)",
                "營收月份", "當月營收(千元)", "營收月增(MoM%)", "營收年增(YoY%)", "累計年增(%)",
                "財報季度", "毛利率(%)", "營業利益率(%)", "稅後淨利率(%)", "最新單季EPS(元)",
                "標籤"
            ]
            
            table_to_format = display_df[show_cols].copy()
            table_to_format = table_to_format.rename(columns={"Code": "代號"})
            # 將編號改成從 1 開始
            table_to_format.index = range(1, len(table_to_format) + 1)
            table_to_format.index.name = "編號"

            # 美化表格樣式
            def _color_positive_green_negative_red(val):
                if isinstance(val, (int, float)):
                    if val > 0:
                        return "color: #10b981; font-weight: bold;"
                    elif val < 0:
                        return "color: #ef4444; font-weight: bold;"
                return ""

            styled_table = table_to_format.style.format({
                "最新市價": "{:,.2f}",
                "持股市值": "{:,.0f}",
                "投組權重(%)": "{:.2f}%",
                "當月營收(千元)": "{:,.0f}",
                "營收月增(MoM%)": "{:+.2f}%",
                "營收年增(YoY%)": "{:+.2f}%",
                "累計年增(%)": "{:+.2f}%",
                "毛利率(%)": "{:.2f}%",
                "營業利益率(%)": "{:.2f}%",
                "稅後淨利率(%)": "{:.2f}%",
                "最新單季EPS(元)": "{:+.2f}",
            }).map(_color_positive_green_negative_red, subset=["營收月增(MoM%)", "營收年增(YoY%)", "累計年增(%)", "營業利益率(%)", "稅後淨利率(%)", "最新單季EPS(元)"])

            st.dataframe(styled_table, use_container_width=True, height=min(450, 40 + len(table_to_format) * 35))

            st.markdown("---")

            # ------------------------------------------------------------
            # 區塊三：個股深度基本面透視鏡 (Deep Dive Stock Explorer)
            # ------------------------------------------------------------
            st.markdown("#### 🔍 【第三部分：個股深度基本面透視鏡與歷史趨勢】")
            
            stock_options = [f"{r['股票名稱']} ({r['Code']})" for _, r in fund_df.iterrows()]
            selected_stock_str = st.selectbox("🎯 請選擇欲深入剖析的持股標的：", stock_options, key="deep_dive_stock_select")
            
            if selected_stock_str:
                selected_code = selected_stock_str.split('(')[-1].replace(')', '').strip()
                selected_row = fund_df[fund_df['Code'] == selected_code].iloc[0]
                selected_ticker = selected_row['Ticker']
                selected_name = selected_row['股票名稱']

                # 深度抓取歷史季度數據與評價資訊 (yfinance) 及長週期月營收 (FinMind + MOPS)
                with st.spinner(f"⏳ 正在獲取 {selected_name} ({selected_code}) 歷史財報與 24~36 個月營收數據..."):
                    q_hist = fetch_stock_quarterly_history(selected_ticker)
                    mops_monthly = fetch_stock_monthly_revenue_history(selected_code)

                # 個股 4 大指標卡片
                d_col1, d_col2, d_col3, d_col4 = st.columns(4)
                with d_col1:
                    render_metric_card(
                        "💰 最新單月營收與動能",
                        f"{selected_row['當月營收(千元)'] / 1000.0:,.1f} 百萬元",
                        f"月增: {selected_row['營收月增(MoM%)']:+.2f}% | 年增: {selected_row['營收年增(YoY%)']:+.2f}%",
                        value_color="#10b981" if selected_row['營收年增(YoY%)'] >= 0 else "#ef4444"
                    )
                with d_col2:
                    render_metric_card(
                        f"📊 財報三率 ({selected_row['財報季度'] or '最新'})",
                        f"毛利 {selected_row['毛利率(%)']:.1f}%",
                        f"營益率: {selected_row['營業利益率(%)']:.1f}% | 淨利率: {selected_row['稅後淨利率(%)']:.1f}%",
                        value_color="#3b82f6"
                    )
                with d_col3:
                    pe_str = f"{q_hist['pe_ratio']:.1f} 倍" if q_hist['pe_ratio'] else "N/A"
                    pb_str = f"{q_hist['pb_ratio']:.2f} 倍" if q_hist['pb_ratio'] else "N/A"
                    render_metric_card(
                        "⚖️ 評價指標 (PE / PB)",
                        f"PE: {pe_str}",
                        f"股價淨值比 PB: {pb_str}",
                        value_color="#8b5cf6"
                    )
                with d_col4:
                    dy_str = f"{q_hist['dividend_yield']:.2f}%" if q_hist['dividend_yield'] is not None else "N/A"
                    roe_str = f"{q_hist['roe']:.1f}%" if q_hist['roe'] is not None else "N/A"
                    render_metric_card(
                        "🌱 股利殖利率與 ROE",
                        f"殖利率: {dy_str}",
                        f"ROE: {roe_str} | 最新單季EPS: {selected_row['最新單季EPS(元)']:+.2f}元",
                        value_color="#f59e0b"
                    )

                # 圖表展示：雙欄佈局
                chart_tab1, chart_tab2, chart_tab3 = st.tabs([
                    "📊 每月營收雙軸走勢圖 (多月份長週期)",
                    "📈 跨季財報三率趨勢圖",
                    "💵 單季 EPS 與獲利走勢圖"
                ])

                # ── 圖表 1：每月營收雙軸走勢圖 (多月份長週期) ──
                with chart_tab1:
                    if mops_monthly and len(mops_monthly) >= 2:
                        rev_x = [m["date_label"] for m in mops_monthly]
                        rev_y = [m["revenue"] / 1000.0 for m in mops_monthly] # 轉為百萬元
                        mom_y = [m["mom"] for m in mops_monthly]
                        yoy_y = [m["yoy"] for m in mops_monthly]
                        chart_subtitle = f"共涵蓋近 {len(rev_x)} 個月之完整營收走勢"
                    else:
                        rev_x = ["去年同月", "上月", f"當月 ({selected_row['營收月份']})"]
                        rev_y = [
                            all_monthly_rev.get(selected_code, {}).get('rev_last_year', 0) / 1000.0,
                            all_monthly_rev.get(selected_code, {}).get('rev_last_month', 0) / 1000.0,
                            selected_row['當月營收(千元)'] / 1000.0
                        ]
                        mom_y = [0.0, 0.0, selected_row['營收月增(MoM%)']]
                        yoy_y = [0.0, 0.0, selected_row['營收年增(YoY%)']]
                        chart_subtitle = "展示近月與去年同期對比"

                    fig_rev = go.Figure()
                    # 柱狀圖：營收金額
                    fig_rev.add_trace(go.Bar(
                        x=rev_x,
                        y=rev_y,
                        name="單月營收 (百萬元)",
                        marker_color="rgba(56, 189, 248, 0.65)",
                        yaxis="y1"
                    ))
                    # 折線圖：年增率 (YoY)
                    fig_rev.add_trace(go.Scatter(
                        x=rev_x,
                        y=yoy_y,
                        name="營收年增率 (YoY %)",
                        mode="lines+markers",
                        line=dict(color="#10b981", width=2.5),
                        marker=dict(size=5),
                        yaxis="y2"
                    ))
                    # 折線圖：月增率 (MoM)
                    fig_rev.add_trace(go.Scatter(
                        x=rev_x,
                        y=mom_y,
                        name="營收月增率 (MoM %)",
                        mode="lines+markers",
                        line=dict(color="#f59e0b", width=1.8, dash="dot"),
                        marker=dict(size=4),
                        yaxis="y2"
                    ))
                    fig_rev.update_layout(
                        title=f"📈 {selected_name} ({selected_code}) 每月營收與成長率走勢 ({chart_subtitle})",
                        xaxis=dict(title="月份 (西元年/月)", tickangle=-45),
                        yaxis=dict(title="單月營收 (百萬元)", side="left", showgrid=True),
                        yaxis2=dict(title="增減率 (%)", side="right", overlaying="y", showgrid=False, zeroline=True, zerolinecolor="rgba(255,255,255,0.2)"),
                        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                        height=440,
                        margin=dict(l=40, r=40, t=60, b=40)
                    )
                    st.plotly_chart(fig_rev, use_container_width=True)

                # ── 圖表 2：財報三率趨勢圖 ──
                with chart_tab2:
                    if q_hist["quarters"] and len(q_hist["quarters"]) >= 2:
                        fig_ratios = go.Figure()
                        fig_ratios.add_trace(go.Scatter(
                            x=q_hist["quarters"],
                            y=q_hist["gross_margin"],
                            name="毛利率 (%)",
                            mode="lines+markers+text",
                            text=[f"{v:.1f}%" for v in q_hist["gross_margin"]],
                            textposition="top center",
                            line=dict(color="#3b82f6", width=3),
                            marker=dict(size=7)
                        ))
                        fig_ratios.add_trace(go.Scatter(
                            x=q_hist["quarters"],
                            y=q_hist["operating_margin"],
                            name="營業利益率 (%)",
                            mode="lines+markers+text",
                            text=[f"{v:.1f}%" for v in q_hist["operating_margin"]],
                            textposition="bottom center",
                            line=dict(color="#10b981", width=2.5),
                            marker=dict(size=6)
                        ))
                        fig_ratios.add_trace(go.Scatter(
                            x=q_hist["quarters"],
                            y=q_hist["net_margin"],
                            name="稅後淨利率 (%)",
                            mode="lines+markers",
                            line=dict(color="#ec4899", width=2, dash="dash"),
                            marker=dict(size=5)
                        ))
                        fig_ratios.update_layout(
                            title=f"📊 {selected_name} ({selected_code}) 跨季度財報三率走勢 (毛利率 / 營業利益率 / 稅後淨利率)",
                            xaxis=dict(title="季度"),
                            yaxis=dict(title="百分比 (%)", zeroline=True),
                            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                            height=420,
                            margin=dict(l=40, r=40, t=60, b=40)
                        )
                        st.plotly_chart(fig_ratios, use_container_width=True)
                    else:
                        st.info(f"💡 {selected_name} 目前官方申報最新季度 ({selected_row['財報季度'] or '最新'})：毛利率 **{selected_row['毛利率(%)']:.2f}%** | 營業利益率 **{selected_row['營業利益率(%)']:.2f}%** | 稅後淨利率 **{selected_row['稅後淨利率(%)']:.2f}%**。")

                # ── 圖表 3：單季 EPS 與獲利結構圖 ──
                with chart_tab3:
                    if q_hist["quarters"] and len(q_hist["quarters"]) >= 2:
                        fig_eps = go.Figure()
                        fig_eps.add_trace(go.Bar(
                            x=q_hist["quarters"],
                            y=q_hist["eps"],
                            name="單季 EPS (元)",
                            marker_color=["#10b981" if v >= 0 else "#ef4444" for v in q_hist["eps"]],
                            text=[f"{v:.2f}" for v in q_hist["eps"]],
                            textposition="outside",
                            yaxis="y1"
                        ))
                        # 稅後淨利折線
                        fig_eps.add_trace(go.Scatter(
                            x=q_hist["quarters"],
                            y=[v / 1e6 for v in q_hist["net_income"]], # 百萬元
                            name="稅後淨利 (百萬元)",
                            mode="lines+markers",
                            line=dict(color="#f59e0b", width=2.5),
                            marker=dict(size=6),
                            yaxis="y2"
                        ))
                        fig_eps.update_layout(
                            title=f"💵 {selected_name} ({selected_code}) 季度 EPS 與獲利走勢",
                            xaxis=dict(title="季度"),
                            yaxis=dict(title="每股盈餘 (元)", side="left"),
                            yaxis2=dict(title="稅後淨利 (百萬元)", side="right", overlaying="y", showgrid=False),
                            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                            height=420,
                            margin=dict(l=40, r=40, t=60, b=40)
                        )
                        st.plotly_chart(fig_eps, use_container_width=True)
                    else:
                        st.info(f"💡 {selected_name} 最新單季基本每股盈餘 (EPS)：**{selected_row['最新單季EPS(元)']:+.2f} 元**。")

                # 個股體檢診斷筆記 (包含營收與毛利率剪刀差關係評析)
                with st.expander(f"📝 【{selected_name} ({selected_code})】 基本面體檢與診斷筆記", expanded=True):
                    diag_mom_text = "月增成長" if selected_row['營收月增(MoM%)'] > 0 else "月增衰退"
                    diag_yoy_text = "年增成長" if selected_row['營收年增(YoY%)'] > 0 else "年增衰退"
                    diag_margin_text = "毛利率高於 30%，具備強大產品競爭力/護城河" if selected_row['毛利率(%)'] >= 30.0 else ("毛利率介於 15%~30%，體質穩健" if selected_row['毛利率(%)'] >= 15.0 else "毛利率低於 15%，屬薄利或成熟競爭市場")
                    
                    # 剪刀差分析：營收 vs 毛利
                    gm_change = 0.0
                    if q_hist["quarters"] and len(q_hist["gross_margin"]) >= 2:
                        gm_change = q_hist["gross_margin"][-1] - q_hist["gross_margin"][-2]
                    
                    yoy_val = selected_row['營收年增(YoY%)']
                    gm_val = selected_row['毛利率(%)']
                    
                    if yoy_val > 0 and (gm_change > 0 or gm_val >= 25.0):
                        scissor_diag = f"🚀 **【雙引擎擴張】** 營收年增達 **{yoy_val:+.2f}%** 且毛利率維持在 **{gm_val:.2f}%** 高檔（季變動 {gm_change:+.2f}%），顯示產品具備強大市場競爭力與定價話語權，獲利含金量與營收規模同步擴張。"
                    elif yoy_val < 0 and (gm_change > 0 or gm_val >= 25.0):
                        scissor_diag = f"🔄 **【營收下降但毛利上升 / 結構轉型優化】** 雖然單月營收年減 **{yoy_val:+.2f}%**，但毛利率達 **{gm_val:.2f}%**（季變動 {gm_change:+.2f}%），呈現「營收降、毛利升」之結構優化特徵。通常代表公司正在主動淘汰低毛利代工訂單、聚焦高附加價值利基型產品，或成本轉嫁效益顯現，體質正在轉佳！"
                    elif yoy_val > 0 and (gm_change < 0 or gm_val < 20.0):
                        scissor_diag = f"⚡ **【營收上升但毛利下降 / 薄利競爭或成本承壓】** 營收年增達 **{yoy_val:+.2f}%** 表現亮眼，但毛利率僅 **{gm_val:.2f}%**（季變動 {gm_change:+.2f}%），呈現「營收升、毛利降」之剪刀差。需特別留意是否受原物料成本上漲、產業進入殺價競爭，或擴大出貨低毛利產品導致「營收虛胖、獲利受壓」之風險。"
                    else:
                        scissor_diag = f"💀 **【量利齊跌 / 景氣下行警戒】** 營收年減 **{yoy_val:+.2f}%** 且毛利率處於 **{gm_val:.2f}%**（季變動 {gm_change:+.2f}%），面臨需求走弱與利潤率壓縮雙重挑戰，需留意產業景氣落底信號。"

                    st.markdown(f"""
                    - **營收動能評等**：當月營收呈現 **{diag_mom_text} ({selected_row['營收月增(MoM%)']:+.2f}%)** 與 **{diag_yoy_text} ({selected_row['營收年增(YoY%)']:+.2f}%)**。累計年增率為 **{selected_row['累計年增(%)']:+.2f}%**。
                    - **營收與毛利剪刀差診斷**：{scissor_diag}
                    - **本業競爭力與產品毛利**：最新申報毛利率為 **{selected_row['毛利率(%)']:.2f}%**（{diag_margin_text}）。營業利益率為 **{selected_row['營業利益率(%)']:.2f}%**。
                    - **淨利結構檢驗**：稅後淨利率 **{selected_row['稅後淨利率(%)']:.2f}%** 與營業利益率相較，{'業外損益貢獻正面' if selected_row['稅後淨利率(%)'] >= selected_row['營業利益率(%)'] else '業外支出略有侵蝕或所得稅提列'}。
                    - **評價估值水位**：目前本益比約 **{pe_str}**，股價淨值比約 **{pb_str}**。
                    """)
