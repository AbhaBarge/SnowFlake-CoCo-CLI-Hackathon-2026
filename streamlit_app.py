import streamlit as st
import pandas as pd
import json

st.set_page_config(page_title="AML Copilot", page_icon="🛡️", layout="wide")

# Works on both Streamlit Community Cloud (via secrets.toml) and SiS (built-in connection)
conn = st.connection("snowflake", type="snowflake")

DB = "AML_COPILOT_DB"
SCHEMA = "RISK_ENGINE"


def fqn(table):
    return f"{DB}.{SCHEMA}.{table}"


@st.cache_data(ttl=120)
def run_query(sql):
    return conn.query(sql)


# --- Sidebar Navigation ---
st.sidebar.title("AML Copilot")
st.sidebar.caption("Risk, Fraud & Regulatory Intelligence")
page = st.sidebar.radio(
    "Navigate",
    ["Executive Summary", "Alert Investigation", "STR Management", "Evidence Viewer", "Risk Analytics"],
)

# ============================================================
# PAGE: Executive Summary
# ============================================================
if page == "Executive Summary":
    st.title("Executive Summary")

    summary = run_query(f"SELECT * FROM {fqn('V_EXECUTIVE_SUMMARY')}")
    row = summary.iloc[0]

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total Transactions", f"{int(row['TOTAL_TRANSACTIONS']):,}")
    c2.metric("Total Alerts", int(row['TOTAL_ALERTS']))
    c3.metric("Critical Alerts", int(row['CRITICAL_ALERTS']))
    c4.metric("STRs Generated", int(row['TOTAL_STRS']))
    c5.metric("Evidence Items", int(row['EVIDENCE_ITEMS']))

    st.divider()
    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("Alerts by Risk Level")
        alert_dist = run_query(f"""
            SELECT RISK_LEVEL, COUNT(*) AS COUNT
            FROM {fqn('ALERTS')}
            GROUP BY RISK_LEVEL
            ORDER BY CASE RISK_LEVEL
                WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2 WHEN 'MEDIUM' THEN 3 ELSE 4 END
        """)
        st.bar_chart(alert_dist, x="RISK_LEVEL", y="COUNT", color="RISK_LEVEL")

    with col_right:
        st.subheader("Alerts by Type")
        alert_type = run_query(f"""
            SELECT ALERT_TYPE, COUNT(*) AS COUNT
            FROM {fqn('ALERTS')}
            GROUP BY ALERT_TYPE ORDER BY COUNT DESC
        """)
        st.bar_chart(alert_type, x="ALERT_TYPE", y="COUNT")

    st.subheader("Risk Score Distribution")
    score_dist = run_query(f"""
        SELECT RISK_LEVEL, COUNT(*) AS COUNT
        FROM {fqn('RISK_SCORES')}
        WHERE TOTAL_SCORE > 0
        GROUP BY RISK_LEVEL
        ORDER BY CASE RISK_LEVEL
            WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2 WHEN 'MEDIUM' THEN 3
            WHEN 'LOW' THEN 4 ELSE 5 END
    """)
    st.bar_chart(score_dist, x="RISK_LEVEL", y="COUNT")

# ============================================================
# PAGE: Alert Investigation
# ============================================================
elif page == "Alert Investigation":
    st.title("Alert Investigation")

    col_f1, col_f2 = st.columns(2)
    with col_f1:
        risk_filter = st.selectbox("Risk Level", ["ALL", "CRITICAL", "HIGH", "MEDIUM"])
    with col_f2:
        type_filter = st.selectbox("Alert Type", ["ALL", "CRITICAL_RISK", "WATCHLIST_HIT", "STRUCTURING", "LAYERING", "MULTI_RULE_TRIGGER"])

    where_clauses = []
    if risk_filter != "ALL":
        where_clauses.append(f"RISK_LEVEL = '{risk_filter}'")
    if type_filter != "ALL":
        where_clauses.append(f"ALERT_TYPE = '{type_filter}'")
    where_sql = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

    alerts = run_query(f"""
        SELECT ALERT_ID, CUSTOMER_ID, ALERT_TYPE, RISK_LEVEL, TOTAL_SCORE,
               TRANSACTION_COUNT, TOTAL_AMOUNT, STATUS, CREATED_AT
        FROM {fqn('ALERTS')}
        {where_sql}
        ORDER BY TOTAL_SCORE DESC
        LIMIT 100
    """)

    st.dataframe(
        alerts,
        use_container_width=True,
        column_config={
            "TOTAL_AMOUNT": st.column_config.NumberColumn(format="$%.2f"),
            "TOTAL_SCORE": st.column_config.ProgressColumn(min_value=0, max_value=100),
        },
    )

    st.divider()
    st.subheader("Alert Detail")
    if not alerts.empty:
        selected_id = st.selectbox("Select Alert ID", alerts["ALERT_ID"].tolist())
        detail = run_query(f"""
            SELECT a.*, c.CUSTOMER_NAME, c.COUNTRY_CODE, c.RISK_TIER, c.IS_PEP
            FROM {fqn('ALERTS')} a
            LEFT JOIN {fqn('CUSTOMERS')} c ON a.CUSTOMER_ID = c.CUSTOMER_ID
            WHERE a.ALERT_ID = {selected_id}
        """)
        if not detail.empty:
            d = detail.iloc[0]
            col_d1, col_d2, col_d3 = st.columns(3)
            col_d1.metric("Customer", f"{d.get('CUSTOMER_NAME', 'N/A')} ({d['CUSTOMER_ID']})")
            col_d2.metric("Country / Risk Tier", f"{d.get('COUNTRY_CODE', 'N/A')} / {d.get('RISK_TIER', 'N/A')}")
            col_d3.metric("PEP Status", "YES" if d.get('IS_PEP') else "NO")

            if d.get("AI_NARRATIVE"):
                st.subheader("AI-Generated Narrative")
                st.info(d["AI_NARRATIVE"])

            st.subheader("Related Transactions")
            txns = run_query(f"""
                SELECT t.TRANSACTION_ID, t.TYPE, t.AMOUNT, t.TRANSACTION_DATE,
                       t.NAMEDEST, rs.TOTAL_SCORE AS RISK_SCORE, rs.RISK_LEVEL
                FROM {fqn('TRANSACTIONS')} t
                JOIN {fqn('RISK_SCORES')} rs ON t.TRANSACTION_ID = rs.TRANSACTION_ID
                WHERE t.NAMEORIG = '{d['CUSTOMER_ID']}'
                  AND rs.RISK_LEVEL IN ('CRITICAL', 'HIGH', 'MEDIUM')
                ORDER BY rs.TOTAL_SCORE DESC
                LIMIT 25
            """)
            st.dataframe(txns, use_container_width=True,
                         column_config={"AMOUNT": st.column_config.NumberColumn(format="$%.2f")})

# ============================================================
# PAGE: STR Management
# ============================================================
elif page == "STR Management":
    st.title("Suspicious Transaction Reports")

    status_filter = st.selectbox("Status Filter", ["ALL", "DRAFT", "REVIEW", "FILED"])
    where_str = f"WHERE STATUS = '{status_filter}'" if status_filter != "ALL" else ""

    strs = run_query(f"""
        SELECT STR_ID, ALERT_ID, SUBJECT_NAME, SUBJECT_ID, TOTAL_AMOUNT,
               SUSPICIOUS_ACTIVITY_TYPE, STATUS, CREATED_AT
        FROM {fqn('STR_REPORTS')}
        {where_str}
        ORDER BY CREATED_AT DESC
        LIMIT 100
    """)

    st.dataframe(strs, use_container_width=True,
                 column_config={"TOTAL_AMOUNT": st.column_config.NumberColumn(format="$%.2f")})

    st.divider()
    if not strs.empty:
        selected_str = st.selectbox("View STR Detail", strs["STR_ID"].tolist())
        str_detail = run_query(f"""
            SELECT * FROM {fqn('STR_REPORTS')} WHERE STR_ID = {selected_str}
        """)
        if not str_detail.empty:
            sd = str_detail.iloc[0]
            st.subheader(f"STR #{sd['STR_ID']} - {sd['SUBJECT_NAME']}")

            mc1, mc2, mc3 = st.columns(3)
            mc1.metric("Subject", f"{sd['SUBJECT_NAME']} ({sd['SUBJECT_ID']})")
            mc2.metric("Amount", f"${sd['TOTAL_AMOUNT']:,.2f}")
            mc3.metric("Status", sd['STATUS'])

            st.subheader("Full Narrative")
            st.text(sd.get("NARRATIVE", "N/A"))

            if sd.get("AI_GENERATED_SUMMARY"):
                st.subheader("AI Summary")
                st.info(sd["AI_GENERATED_SUMMARY"])

# ============================================================
# PAGE: Evidence Viewer
# ============================================================
elif page == "Evidence Viewer":
    st.title("Evidence Packages")

    evidence_list = run_query(f"""
        SELECT ep.EVIDENCE_ID, ep.STR_ID, ep.ALERT_ID, ep.PACKAGE_TYPE,
               s.SUBJECT_NAME, s.TOTAL_AMOUNT, ep.CREATED_AT
        FROM {fqn('EVIDENCE_PACKAGES')} ep
        LEFT JOIN {fqn('STR_REPORTS')} s ON ep.STR_ID = s.STR_ID
        WHERE ep.PACKAGE_TYPE = 'FULL_EVIDENCE_PACKAGE'
        ORDER BY ep.CREATED_AT DESC
        LIMIT 50
    """)
    st.dataframe(evidence_list, use_container_width=True)

    if not evidence_list.empty:
        selected_ev = st.selectbox("View Evidence Package", evidence_list["EVIDENCE_ID"].tolist())
        ev_detail = run_query(f"""
            SELECT EVIDENCE_DATA FROM {fqn('EVIDENCE_PACKAGES')}
            WHERE EVIDENCE_ID = {selected_ev}
        """)
        if not ev_detail.empty:
            data = ev_detail.iloc[0]["EVIDENCE_DATA"]
            if isinstance(data, str):
                data = json.loads(data)
            st.json(data)

# ============================================================
# PAGE: Risk Analytics
# ============================================================
elif page == "Risk Analytics":
    st.title("Risk Analytics")

    st.subheader("Top 20 Highest-Risk Customers")
    top_customers = run_query(f"""
        SELECT
            rs.CUSTOMER_ID,
            c.CUSTOMER_NAME,
            c.COUNTRY_CODE,
            c.RISK_TIER,
            ROUND(MAX(rs.TOTAL_SCORE), 2) AS MAX_SCORE,
            COUNT(*) AS FLAGGED_TXNS,
            ROUND(SUM(t.AMOUNT), 2) AS TOTAL_FLAGGED_AMOUNT
        FROM {fqn('RISK_SCORES')} rs
        JOIN {fqn('TRANSACTIONS')} t ON rs.TRANSACTION_ID = t.TRANSACTION_ID
        LEFT JOIN {fqn('CUSTOMERS')} c ON rs.CUSTOMER_ID = c.CUSTOMER_ID
        WHERE rs.RISK_LEVEL IN ('CRITICAL', 'HIGH')
        GROUP BY rs.CUSTOMER_ID, c.CUSTOMER_NAME, c.COUNTRY_CODE, c.RISK_TIER
        ORDER BY MAX_SCORE DESC, TOTAL_FLAGGED_AMOUNT DESC
        LIMIT 20
    """)
    st.dataframe(top_customers, use_container_width=True,
                 column_config={
                     "TOTAL_FLAGGED_AMOUNT": st.column_config.NumberColumn(format="$%.2f"),
                     "MAX_SCORE": st.column_config.ProgressColumn(min_value=0, max_value=100),
                 })

    st.divider()
    col_a1, col_a2 = st.columns(2)

    with col_a1:
        st.subheader("Risk by Transaction Type")
        type_risk = run_query(f"""
            SELECT t.TYPE, rs.RISK_LEVEL, COUNT(*) AS COUNT
            FROM {fqn('RISK_SCORES')} rs
            JOIN {fqn('TRANSACTIONS')} t ON rs.TRANSACTION_ID = t.TRANSACTION_ID
            WHERE rs.RISK_LEVEL IN ('CRITICAL', 'HIGH', 'MEDIUM')
            GROUP BY t.TYPE, rs.RISK_LEVEL
            ORDER BY t.TYPE, rs.RISK_LEVEL
        """)
        st.dataframe(type_risk, use_container_width=True)

    with col_a2:
        st.subheader("Watchlist Matches")
        wl_matches = run_query(f"""
            SELECT c.CUSTOMER_NAME, c.CUSTOMER_ID, w.LIST_SOURCE, w.ENTITY_TYPE,
                   COUNT(DISTINCT rs.TRANSACTION_ID) AS FLAGGED_TXNS
            FROM {fqn('CUSTOMERS')} c
            JOIN {fqn('WATCHLISTS')} w ON c.CUSTOMER_NAME = w.ENTITY_NAME AND w.IS_ACTIVE = TRUE
            LEFT JOIN {fqn('RISK_SCORES')} rs ON c.CUSTOMER_ID = rs.CUSTOMER_ID
            GROUP BY c.CUSTOMER_NAME, c.CUSTOMER_ID, w.LIST_SOURCE, w.ENTITY_TYPE
            ORDER BY FLAGGED_TXNS DESC
        """)
        st.dataframe(wl_matches, use_container_width=True)

    st.subheader("Daily Transaction Volume (Last 90 Days)")
    daily_vol = run_query(f"""
        SELECT TRANSACTION_DATE::DATE AS TXN_DATE,
               COUNT(*) AS TXN_COUNT,
               ROUND(SUM(AMOUNT), 2) AS TOTAL_AMOUNT
        FROM {fqn('TRANSACTIONS')}
        GROUP BY TXN_DATE
        ORDER BY TXN_DATE
    """)
    st.line_chart(daily_vol, x="TXN_DATE", y="TXN_COUNT")
