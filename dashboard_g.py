import streamlit as st
import pandas as pd
import re
import os

# ------------------------------------------------------------------------------
# 기본 설정
# ------------------------------------------------------------------------------

st.set_page_config(
    page_title="아크모터스 통합검색 대시보드",
    layout="wide",
)

# 전체 폰트 사이즈 및 스타일 조정
st.markdown(
    """
    <style>
    html, body, [class*="css"] {
        font-size: 16px;
        font-family: 'Pretendard', 'Malgun Gothic', sans-serif;
    }
    .stMetric label {
        font-size: 16px !important;
    }
    /* 탭 스타일 강조 */
    .stTabs [data-baseweb="tab"] {
        font-size: 16px;
        font-weight: bold;
    }
    </style>
    """,
    unsafe_allow_html=True
)

# ------------------------------------------------------------------------------
# 파일 경로 설정 (호환성 보완)
# ------------------------------------------------------------------------------

# 1. 통합 파일 경로 (사용자 제공 코드 기준)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MASTER_PATH = os.path.join(BASE_DIR, "amotors_master_data.xlsx")

# 2. 개별 파일 경로 (기존 amotors_V2 폴더 기준 - 백업용)
DESKTOP_PATH = os.path.join(os.path.expanduser("~"), "Desktop")
AMOTORS_PATH = os.path.join(DESKTOP_PATH, "amotors_V2")

FILE_PATHS = {
    "income": os.path.join(AMOTORS_PATH, "아크 모터스 사업소득.xlsx"),
    "purchase": os.path.join(AMOTORS_PATH, "원본_재활용폐자원세액공제신고서.xlsx"),
    "ledger": os.path.join(AMOTORS_PATH, "♣장부♣ 10.xlsx"),
    "inventory": os.path.join(AMOTORS_PATH, "상품내역.xlsx"),
    "report": os.path.join(AMOTORS_PATH, "결산 보고서.xlsx"),
}

# ------------------------------------------------------------------------------
# 헬퍼 함수들
# ------------------------------------------------------------------------------

def clean_numeric(series):
    """숫자형 문자열을 정수(int)로 변환"""
    return pd.to_numeric(
        series.astype(str).str.replace(r"[^0-9.-]", "", regex=True),
        errors="coerce"
    ).fillna(0).astype(int)

def normalize_ym(series):
    """
    기준년월(YYYY-MM) 컬럼을 'YYYY-MM' 문자열로 통일
    """
    s = pd.to_datetime(series, errors="coerce")
    out = s.dt.strftime("%Y-%m")
    # 날짜 변환 실패한 값은 원본 그대로 유지 (문자열일 수 있음)
    mask_nat = s.isna()
    if mask_nat.any():
        out = out.astype(object) # 호환성 확보
        out[mask_nat] = series.astype(str)[mask_nat]
    return out

def format_currency(df, cols):
    """
    지정된 컬럼들을 천단위 콤마가 들어간 문자열로 변환 (표시용)
    """
    df = df.copy()
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)
            df[c] = df[c].map(lambda x: f"{x:,}")
    return df

# 결산에서 제외할 소득구분 값들
EXCLUDE_INCOME_KEYWORDS = ["외부", "비직원", "기타", "제외"]

def is_excluded_income_type(value: str) -> bool:
    if pd.isna(value):
        return False
    v = str(value).strip()
    if v == "":
        return False
    lower_v = v.lower()
    for kw in EXCLUDE_INCOME_KEYWORDS:
        if kw.lower() in lower_v:
            return True
    return False

def categorize_ledger_row(row):
    """장부 행 자동 분류 로직"""
    # 컬럼명 호환성 체크 (기존 장부 파일 vs 새 코드)
    # 기존 장부 파일의 '계정' 관련 컬럼명이 다양할 수 있음
    acc_col = next((c for c in row.index if "계정" in str(c)), "")
    account = str(row.get(acc_col, ""))
    desc = str(row.get("내용", ""))

    text = (account + " " + desc).lower()

    if "차대" in account or "상사이전" in account or "매입" in desc:
        return "차량매입"
    if "판매" in desc or "매출" in desc:
        return "매출"
    for kw in ["급여", "인건비", "상여", "일당", "급료", "4대보험"]:
        if kw in desc:
            return "인건비"
    for kw in ["임대료", "월세", "전세", "보증금", "건물관리비", "관리비"]:
        if kw in desc:
            return "고정비"
    for kw in ["부가세", "소득세", "원천세", "지방세", "세금"]:
        if kw in desc:
            return "세금"
    for kw in ["광고", "홍보", "수수료", "카드수수료", "통신비", "전기료", "소모품", "잡비", "유류", "주유"]:
        if kw in desc:
            return "변동비"
    if "광택" in desc or "판금" in desc or "정비" in desc or "수리" in desc:
        return "변동비"

    return "기타"

@st.cache_data
def load_data(master_path: str):
    """
    데이터 로드 함수 (통합 파일 우선, 없으면 개별 파일 로드 시도)
    주의: @st.cache_data 사용 시 함수 내부에서 st.toast, st.error 등 UI 요소 호출 금지
    """
    # 1. 통합 파일(amotors_master_data.xlsx)이 있는지 확인
    if os.path.exists(master_path):
        try:
            xls = pd.ExcelFile(master_path)
            df_emp = xls.parse("1_직원소득")
            df_pur = xls.parse("2_차량매입")
            df_led = xls.parse("3_장부")
            df_inv = xls.parse("4_차량상품화")
            df_month = xls.parse("5_월별결산")
            # 성공 시 별도 UI 출력 없이 데이터만 반환
        except Exception as e:
            # 실패 시 None 반환 (에러는 호출부에서 처리)
            print(f"통합 파일 로드 중 오류 발생: {e}")
            return None
    else:
        # 2. 통합 파일이 없으면 기존 개별 파일들 로드 시도 (amotors_V2 폴더)
        if not os.path.exists(FILE_PATHS["income"]):
            # 파일 없음
            return None
        
        # (1) 직원 소득
        try:
            # 기존 파일 구조에 맞춰 컬럼 매핑
            temp = pd.read_excel(FILE_PATHS["income"], header=7) # 기존 구조 가정
            temp = temp.loc[:, ~temp.columns.str.contains('^Unnamed')]
            # 컬럼명 표준화 (새 코드 로직에 맞춤)
            df_emp = temp.rename(columns={
                "성명": "직원명", 
                "귀속년월": "기준년월(YYYY-MM)", 
                "지급 날짜": "지급일자"
            })
            # 없는 컬럼 추가
            if "소득구분(고정/변동/퇴사 등)" not in df_emp.columns:
                df_emp["소득구분(고정/변동/퇴사 등)"] = "직원"
        except: df_emp = pd.DataFrame()

        # (2) 차량 매입
        try:
            temp = pd.read_excel(FILE_PATHS["purchase"], header=8)
            df_pur = temp.loc[:, ~temp.columns.str.contains('^Unnamed')]
            df_pur["기준년월(YYYY-MM)"] = pd.to_datetime(df_pur["취득일자"], errors='coerce').dt.strftime('%Y-%m')
        except: df_pur = pd.DataFrame()

        # (3) 장부
        try:
            temp = pd.read_excel(FILE_PATHS["ledger"], sheet_name="장부", header=2)
            df_led = temp.loc[:, ~temp.columns.str.contains('^Unnamed')]
            df_led = df_led.rename(columns={"계정": "계정구분(장부/부가세/차대/이전비/상사이전/미수금/일계표/결산/기타)"})
            df_led["기준년월(YYYY-MM)"] = pd.to_datetime(df_led["일자"], errors='coerce').dt.strftime('%Y-%m')
            if "관련직원명" not in df_led.columns: df_led["관련직원명"] = ""
        except: df_led = pd.DataFrame()

        # (4) 차량 상품화 (없으면 빈 DF)
        if os.path.exists(FILE_PATHS["inventory"]):
            try:
                temp = pd.read_excel(FILE_PATHS["inventory"])
                df_inv = temp
                if "기준년월(YYYY-MM)" not in df_inv.columns and "입고일자" in df_inv.columns:
                    df_inv["기준년월(YYYY-MM)"] = pd.to_datetime(df_inv["입고일자"], errors='coerce').dt.strftime('%Y-%m')
            except: df_inv = pd.DataFrame()
        else:
            df_inv = pd.DataFrame(columns=["차량번호", "담당자", "비용(VAT포함)", "기준년월(YYYY-MM)"])

        # (5) 월별 결산 (없으면 빈 DF)
        if os.path.exists(FILE_PATHS["report"]):
            try:
                df_month = pd.read_excel(FILE_PATHS["report"])
            except: df_month = pd.DataFrame()
        else:
            df_month = pd.DataFrame()

    # 공통 전처리: 기준년월 컬럼 표준화
    for df in [df_emp, df_pur, df_led, df_inv, df_month]:
        if "기준년월(YYYY-MM)" in df.columns:
            df["기준년월"] = normalize_ym(df["기준년월(YYYY-MM)"])
        elif "기준년월" not in df.columns:
            df["기준년월"] = ""

    # 날짜 컬럼 표준화 (datetime -> date)
    date_cols_map = {
        "emp": ["지급일자"],
        "pur": ["취득일자"],
        "led": ["일자"],
        "inv": ["입고일자", "상품화완료일자"],
    }
    
    df_dict = {"emp": df_emp, "pur": df_pur, "led": df_led, "inv": df_inv, "month": df_month}
    
    for key, cols in date_cols_map.items():
        for c in cols:
            if c in df_dict[key].columns:
                df_dict[key][c] = pd.to_datetime(df_dict[key][c], errors="coerce").dt.date
    
    return df_dict

def compute_auto_month_summary(df_emp, df_pur, df_led, df_inv, ym: str):
    """
    자동 결산 요약 생성
    """
    rows = []

    # 1) 인건비
    if not df_emp.empty:
        emp_month = df_emp[df_emp["기준년월"] == ym].copy()
        col_income_type = "소득구분(고정/변동/퇴사 등)"
        if col_income_type in emp_month.columns:
            exclude_mask = emp_month[col_income_type].apply(is_excluded_income_type)
            emp_month = emp_month[~exclude_mask]
        
        # 숫자 변환 후 합계
        if "정산입금액" in emp_month.columns:
            total_emp = int(clean_numeric(emp_month["정산입금액"]).sum())
            if total_emp != 0:
                rows.append([ym, "인건비", "직원소득(정산입금액)", total_emp, "직원소득", "외부/비직원 제외"])

    # 2) 차량매입
    if not df_pur.empty and "매입가액" in df_pur.columns:
        pur_month = df_pur[df_pur["기준년월"] == ym]
        total_pur = int(clean_numeric(pur_month["매입가액"]).sum())
        if total_pur != 0:
            rows.append([ym, "차량매입", "재활용차량 매입가액", total_pur, "차량매입", ""])

    # 3) 차량상품화
    if not df_inv.empty and "비용(VAT포함)" in df_inv.columns:
        inv_month = df_inv[df_inv["기준년월"] == ym]
        total_inv = int(clean_numeric(inv_month["비용(VAT포함)"]).sum())
        if total_inv != 0:
            rows.append([ym, "변동비", "차량 상품화비", total_inv, "차량상품화", ""])

    # 4) 장부
    if not df_led.empty:
        led_month = df_led[df_led["기준년월"] == ym].copy()
        if not led_month.empty:
            led_month["자동분류"] = led_month.apply(categorize_ledger_row, axis=1)
            
            # 매출 (입금 기준)
            if "입금" in led_month.columns:
                income_rows = led_month[(clean_numeric(led_month["입금"]) > 0) & (led_month["자동분류"] == "매출")]
                total_sales = int(clean_numeric(income_rows["입금"]).sum())
                if total_sales != 0:
                    rows.append([ym, "매출", "장부 매출(입금)", total_sales, "장부", ""])

            # 비용 (출금 기준)
            if "출금" in led_month.columns:
                led_month["출금_int"] = clean_numeric(led_month["출금"])
                expense_rows = led_month[led_month["출금_int"] > 0].copy()
                if not expense_rows.empty:
                    grp = expense_rows.groupby("자동분류")["출금_int"].sum()
                    for cat, val in grp.items():
                        if cat == "매출" or val == 0: continue
                        rows.append([ym, cat, f"장부 출금({cat})", int(val), "장부", "자동분류"])

    return pd.DataFrame(rows, columns=[
        "기준년월", "항목구분(매출/차량매입/고정비/변동비/인건비/세금/기타)", 
        "세부항목", "금액", "데이터출처(직원소득/차량매입/장부/차량상품화/수동)", "비고"
    ])

# ------------------------------------------------------------------------------
# 메인 로직
# ------------------------------------------------------------------------------

st.sidebar.title("🚘 아크모터스")
st.sidebar.caption("통합검색 시스템 v2.0")

file_path = st.sidebar.text_input(
    "통합 데이터 파일 경로 (선택사항)",
    value=DEFAULT_MASTER_PATH,
    help="통합 파일이 없으면 자동으로 기존 'amotors_V2' 폴더의 파일들을 로드합니다."
)

# 데이터 로드 실행
data = load_data(file_path)

if data is None:
    st.error("데이터를 불러올 수 없습니다. 파일 경로를 확인해주세요.\n\n"
             f"- 통합 파일 경로: {file_path}\n"
             f"- 개별 파일 경로(폴더): {AMOTORS_PATH}")
    st.stop()

df_emp = data["emp"]
df_pur = data["pur"]
df_led = data["led"]
df_inv = data["inv"]
df_month = data["month"]

# ------------------------------------------------------------------------------
# 사이드바: 검색 모드 선택
# ------------------------------------------------------------------------------

mode = st.sidebar.radio(
    "검색 유형 선택",
    ["직원 통합검색", "차량 통합검색", "월별 결산 보기", "원시 시트 보기"]
)

st.sidebar.markdown("---")

# ------------------------------------------------------------------------------
# 1. 직원 통합검색
# ------------------------------------------------------------------------------

if mode == "직원 통합검색":
    st.title("👤 직원 통합검색")

    # 직원명 후보 통합 (concat 사용)
    names_list = []
    if "직원명" in df_emp.columns:
        names_list.append(df_emp["직원명"].dropna().astype(str))
    if "담당자" in df_led.columns:
        names_list.append(df_led["담당자"].dropna().astype(str))
    if "관련직원명" in df_led.columns:
        names_list.append(df_led["관련직원명"].dropna().astype(str))
    if "담당자" in df_inv.columns:
        names_list.append(df_inv["담당자"].dropna().astype(str))
    
    if names_list:
        names = pd.concat(names_list).unique()
        names = sorted([n for n in names if n.strip() != ""])
    else:
        names = []

    st.markdown("#### 1) 직원명 검색")
    
    # 검색 UI 개선
    col_search, col_sel = st.columns([1, 2])
    with col_search:
        search_query = st.text_input("이름 검색 (엔터)", placeholder="홍길동")
    
    candidate_names = names
    if search_query:
        candidate_names = [n for n in names if search_query.lower() in n.lower()]
        if not candidate_names:
            st.warning("검색 결과가 없습니다.")
            candidate_names = names # 결과 없으면 전체 표시

    with col_sel:
        selected_name = st.selectbox("직원 선택", options=candidate_names)

    if not selected_name:
        st.info("직원을 선택해주세요.")
        st.stop()

    st.divider()
    st.markdown(f"### 🔍 **{selected_name}** 님 상세 리포트")

    # --- 1) 직원 소득 요약
    st.subheader("① 직원 소득 (사업소득)")
    
    if "직원명" in df_emp.columns:
        emp_rows = df_emp[df_emp["직원명"] == selected_name]
    else:
        emp_rows = pd.DataFrame()

    if emp_rows.empty:
        st.info("등록된 사업소득 데이터가 없습니다.")
    else:
        # 숫자형 변환 보장
        total_income = int(clean_numeric(emp_rows["정산입금액"]).sum())
        total_tax = int(clean_numeric(emp_rows["소득세"]).sum() + clean_numeric(emp_rows["주민세"]).sum())
        
        c1, c2, c3 = st.columns(3)
        c1.metric("누적 정산입금액", f"{total_income:,} 원")
        c2.metric("누적 세금 (소득+주민)", f"{total_tax:,} 원")
        c3.metric("지급 건수", f"{len(emp_rows)} 건")

        # 상세 표
        display_cols = ["기준년월", "지급일자", "과세표준", "소득세", "주민세", "정산입금액", "비고"]
        cols_in_df = [c for c in display_cols if c in emp_rows.columns]
        emp_display = format_currency(emp_rows[cols_in_df], ["과세표준", "소득세", "주민세", "정산입금액"])
        st.dataframe(emp_display, use_container_width=True, hide_index=True)

    st.markdown("---")

    # --- 2) 장부 내역
    st.subheader("② 장부 입출금 내역")
    
    if "담당자" in df_led.columns:
        led_rows = df_led[
            (df_led["담당자"] == selected_name) | 
            (df_led.get("관련직원명", pd.Series([""]*len(df_led))) == selected_name)
        ]
    else:
        led_rows = pd.DataFrame()

    if led_rows.empty:
        st.info("관련된 장부 내역이 없습니다.")
    else:
        t_in = int(clean_numeric(led_rows["입금"]).sum())
        t_out = int(clean_numeric(led_rows["출금"]).sum())
        
        c1, c2 = st.columns(2)
        c1.metric("총 입금 기여", f"{t_in:,} 원")
        c2.metric("총 출금 (비용)", f"{t_out:,} 원")
        
        led_disp = format_currency(led_rows, ["입금", "출금", "잔액"])
        # 주요 컬럼만 표시
        main_cols = ["일자", "계정구분(장부/부가세/차대/이전비/상사이전/미수금/일계표/결산/기타)", "내용", "차량번호", "입금", "출금"]
        cols_to_show = [c for c in main_cols if c in led_disp.columns]
        st.dataframe(led_disp[cols_to_show], use_container_width=True, hide_index=True)

    st.markdown("---")

    # --- 3) 차량 상품화
    st.subheader("③ 차량 상품화 담당 내역")
    if "담당자" in df_inv.columns:
        inv_rows = df_inv[df_inv["담당자"] == selected_name]
    else:
        inv_rows = pd.DataFrame()

    if inv_rows.empty:
        st.info("담당한 상품화 내역이 없습니다.")
    else:
        t_cost = int(clean_numeric(inv_rows["비용(VAT포함)"]).sum())
        st.metric("상품화 총 비용", f"{t_cost:,} 원")
        
        inv_disp = format_currency(inv_rows, ["비용(VAT포함)"])
        st.dataframe(inv_disp, use_container_width=True, hide_index=True)

# ------------------------------------------------------------------------------
# 2. 차량 통합검색
# ------------------------------------------------------------------------------

elif mode == "차량 통합검색":
    st.title("🚗 차량 통합검색")

    # 차량번호 후보 통합
    cars_list = []
    if "차량번호" in df_pur.columns: cars_list.append(df_pur["차량번호"].dropna().astype(str))
    if "차량번호" in df_led.columns: cars_list.append(df_led["차량번호"].dropna().astype(str))
    if "차량번호" in df_inv.columns: cars_list.append(df_inv["차량번호"].dropna().astype(str))
    
    if cars_list:
        car_nums = pd.concat(cars_list).unique()
        car_nums = sorted([c for c in car_nums if c.strip() != ""])
    else:
        car_nums = []

    st.markdown("#### 1) 차량번호 검색")
    col_search, col_sel = st.columns([1, 2])
    with col_search:
        car_query = st.text_input("차량번호 검색 (엔터)", placeholder="1234")
    
    cand_cars = car_nums
    if car_query:
        cand_cars = [c for c in car_nums if car_query.lower() in c.lower()]
        if not cand_cars:
            st.warning("검색 결과가 없습니다.")
            cand_cars = car_nums

    with col_sel:
        selected_car = st.selectbox("차량 선택", options=cand_cars)

    if not selected_car:
        st.info("차량을 선택해주세요.")
        st.stop()

    st.divider()
    st.markdown(f"### 🔍 **{selected_car}** 상세 정보")

    # 1) 매입 정보
    st.subheader("① 차량 매입 정보")
    if "차량번호" in df_pur.columns:
        pur_rows = df_pur[df_pur["차량번호"] == selected_car]
        if not pur_rows.empty:
            pur_val = int(clean_numeric(pur_rows["매입가액"]).sum())
            st.metric("매입가액", f"{pur_val:,} 원")
            st.dataframe(format_currency(pur_rows, ["매입가액"]), use_container_width=True, hide_index=True)
        else:
            st.info("매입 데이터가 없습니다.")
    else:
        st.info("매입 데이터 컬럼 오류")

    st.markdown("---")

    # 2) 장부 내역
    st.subheader("② 장부 입출금 내역")
    if "차량번호" in df_led.columns:
        led_rows = df_led[df_led["차량번호"] == selected_car]
        if not led_rows.empty:
            t_in = int(clean_numeric(led_rows["입금"]).sum())
            t_out = int(clean_numeric(led_rows["출금"]).sum())
            c1, c2 = st.columns(2)
            c1.metric("이 차량으로 발생한 입금", f"{t_in:,} 원")
            c2.metric("이 차량에 쓴 출금", f"{t_out:,} 원")
            st.dataframe(format_currency(led_rows, ["입금", "출금", "잔액"]), use_container_width=True, hide_index=True)
        else:
            st.info("장부 내역이 없습니다.")
            
    st.markdown("---")

    # 3) 상품화 내역
    st.subheader("③ 상품화 내역")
    if "차량번호" in df_inv.columns:
        inv_rows = df_inv[df_inv["차량번호"] == selected_car]
        if not inv_rows.empty:
            t_cost = int(clean_numeric(inv_rows["비용(VAT포함)"]).sum())
            st.metric("상품화 비용 합계", f"{t_cost:,} 원")
            st.dataframe(format_currency(inv_rows, ["비용(VAT포함)"]), use_container_width=True, hide_index=True)
        else:
            st.info("상품화 내역이 없습니다.")

# ------------------------------------------------------------------------------
# 3. 월별 결산 보기
# ------------------------------------------------------------------------------

elif mode == "월별 결산 보기":
    st.title("📅 월별 결산 보기")

    # 기준년월 수집
    ym_set = set()
    for df in [df_emp, df_pur, df_led, df_inv]:
        if "기준년월" in df.columns:
            ym_set.update(df["기준년월"].dropna().unique())
    
    ym_list = sorted([y for y in ym_set if str(y).strip() != ""])
    
    if not ym_list:
        st.warning("날짜 데이터가 없어 결산을 조회할 수 없습니다.")
        st.stop()

    selected_ym = st.selectbox("조회할 년월 선택", ym_list)

    st.subheader(f"📊 {selected_ym} 자동 결산 요약")
    
    auto_df = compute_auto_month_summary(df_emp, df_pur, df_led, df_inv, selected_ym)
    
    if auto_df.empty:
        st.info("해당 월의 데이터가 없습니다.")
    else:
        # 차트용 데이터
        chart_data = auto_df.groupby("항목구분(매출/차량매입/고정비/변동비/인건비/세금/기타)")["금액"].sum()
        st.bar_chart(chart_data)
        
        # 상세 표
        st.dataframe(format_currency(auto_df, ["금액"]), use_container_width=True, hide_index=True)

# ------------------------------------------------------------------------------
# 4. 원시 시트 보기
# ------------------------------------------------------------------------------

elif mode == "원시 시트 보기":
    st.title("📂 원시 데이터 확인")
    
    sheet_map = {
        "직원소득(사업소득)": df_emp,
        "차량매입(폐자원)": df_pur,
        "장부": df_led,
        "차량상품화": df_inv,
        "월별결산(보고서)": df_month
    }
    
    sel_sheet = st.selectbox("확인할 데이터 선택", list(sheet_map.keys()))
    
    st.markdown(f"### {sel_sheet}")
    df_show = sheet_map[sel_sheet]
    
    if df_show.empty:
        st.warning("데이터가 비어있습니다.")
    else:
        # 금액 컬럼 포맷팅 시도
        money_candidates = ["입금", "출금", "잔액", "금액", "매입가액", "정산입금액", "과세표준", "비용(VAT포함)"]
        df_show = format_currency(df_show, money_candidates)
        st.dataframe(df_show, use_container_width=True)