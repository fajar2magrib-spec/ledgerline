
from datetime import date
import pandas as pd
"""Unit tests for the pure report maths (aging, client totals, tax, statements)."""
from ledgerline import reportcalc as rc

def frame():
    rows = [
     # id, number, client, issue, due, status, display, subtotal, disc, tax, total, paid
     (1,"INV-1","Acme","2026-06-01","2026-06-15","Pending","Overdue",1000,0,100,1100,0),
     (2,"INV-2","Acme","2026-09-01","2026-10-20","Partially Paid","Partially Paid",500,50,45,495,95),
     (3,"INV-3","Beta","2026-08-01","2026-08-20","Paid","Paid",200,0,0,200,200),
     (4,"INV-4","Beta","2026-09-10","2026-09-20","Cancelled","Cancelled",300,0,0,300,0),
     (5,"INV-5","Beta","2026-09-12","2026-09-30","Pending","Overdue",100,0,0,100,0),
    ]
    df = pd.DataFrame(rows, columns=["id","number","client","issue_date","due_date","status","display_status","subtotal","discount_total","tax_total","total","amount_paid"])
    df["issue_date"]=pd.to_datetime(df["issue_date"]); df["due_date"]=pd.to_datetime(df["due_date"])
    act = df["status"]!="Cancelled"
    df["balance"]=(df["total"]-df["amount_paid"]).where(act,0.0)
    df["billed"]=df["total"].where(act,0.0); df["collected"]=df["amount_paid"].where(act,0.0); df["outstanding"]=df["balance"]
    return df


def test_report_maths():
    today = date(2026,10,6)
    df = frame()

    # buckets
    assert [rc.aging_bucket(d) for d in (-5,0,1,30,31,60,61,90,91)] == ["Not yet due","Not yet due","1-30 days","1-30 days","31-60 days","31-60 days","61-90 days","61-90 days","90+ days"]
    det = rc.aging_detail(df, today)
    assert list(det["number"]) == ["INV-1","INV-5","INV-2"], det        # oldest first, paid/cancelled excluded
    assert det.loc[det.number=="INV-1","days_overdue"].item() == 113 and det.loc[det.number=="INV-1","bucket"].item()=="90+ days"
    assert det.loc[det.number=="INV-5","bucket"].item()=="1-30 days"
    assert det.loc[det.number=="INV-2","bucket"].item()=="Not yet due" and det.loc[det.number=="INV-2","days_overdue"].item()==0
    summ = rc.aging_summary(det)
    assert list(summ["bucket"])==list(rc.AGING_BUCKETS) and summ["invoices"].sum()==3
    assert abs(summ["balance"].sum() - (1100+400+100)) < 1e-9
    # empty inputs never crash
    e = df.iloc[0:0]
    assert rc.aging_summary(rc.aging_detail(e,today))["balance"].sum()==0
    assert rc.client_summary(e).empty and rc.monthly_tax(e).empty and rc.statement(e,"x").empty

    cs = rc.client_summary(df).set_index("client")
    assert cs.loc["Acme","invoices"]==2 and cs.loc["Beta","invoices"]==2          # cancelled excluded
    assert cs.loc["Acme","billed"]==1595 and cs.loc["Acme","outstanding"]==1500 and cs.loc["Acme","overdue"]==1100
    assert cs.loc["Beta","collected"]==200 and cs.loc["Beta","overdue"]==100
    assert list(rc.client_summary(df)["client"])==["Acme","Beta"]
    tx = rc.monthly_tax(df)
    assert tx["tax"].sum()==145 and len(tx)==3 and tx.iloc[0]["month"]==pd.Timestamp("2026-09-01")
    st = rc.statement(df,"Acme")
    assert list(st["number"])==["INV-1","INV-2"] and list(st["running_balance"])==[1100,1500]



def test_profit_and_loss_and_expense_categories():
    import pandas as pd
    df = pd.DataFrame({"number": ["A", "B", "C"], "status": ["Paid", "Pending", "Cancelled"],
                       "issue_date": pd.to_datetime(["2026-09-03", "2026-10-02", "2026-10-03"]),
                       "revenue": [1000.0, 500.0, 0.0], "expenses": [100.0, 50.0, 0.0]})
    exp = pd.DataFrame({"id": [1, 2, 3], "category": ["Advertising", "Advertising", "Software & tools"],
                        "spent_on": pd.to_datetime(["2026-10-05", "2026-08-01", "2026-10-09"]), "amount": [200.0, 40.0, 60.0]})
    pl = rc.profit_and_loss(df, exp).set_index("month")
    assert list(pl.index)[0] == pd.Timestamp("2026-10-01")                    # newest first
    assert pl.loc["2026-10-01", "net_profit"] == 500 - 50 - 260              # 190
    assert pl.loc["2026-09-01", "net_profit"] == 900
    assert pl.loc["2026-08-01", "net_profit"] == -40                         # expenses-only month
    assert rc.profit_and_loss(df.iloc[0:0], exp.iloc[0:0]).empty
    assert rc.profit_and_loss(df, exp.iloc[0:0])["net_profit"].sum() == 900 + 450
    cats = rc.expense_by_category(exp)
    assert list(cats["category"]) == ["Advertising", "Software & tools"] and cats.iloc[0]["amount"] == 240
    assert rc.expense_by_category(exp.iloc[0:0]).empty
