STYLE = """<style>
:root {color-scheme:light;--ink:#172f40;--muted:#526575;--line:#dce4ea;--navy:#16354a;--teal:#087e83;}
.stApp,[data-testid="stAppViewContainer"] {background:#f5f7fa;color:var(--ink);font-family:'Segoe UI',Arial,sans-serif;}
[data-testid="stHeader"] {background:#f5f7fa;}
[data-testid="stToolbar"],#MainMenu {display:none;}
[data-testid="stSidebar"] {background:#fff;border-right:1px solid var(--line);}
[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {padding-top:1.4rem;}
.brand {padding:0 0 1.7rem;display:flex;flex-direction:column;gap:.4rem;color:var(--navy);}
.brand b {font-size:1.45rem;letter-spacing:-.04em;}
.brand span {font-size:.65rem;letter-spacing:.12em;color:var(--muted);}
[data-testid="stPageLink"] a {padding:.7rem .8rem;border-radius:6px;}
[data-testid="stPageLink"] a:hover {background:#eef5f6;}
[data-testid="stPageLink"] a[aria-current="page"] {background:#e5f1f2;color:#075e65;font-weight:650;}
.block-container {max-width:1500px;padding:2.1rem 2.4rem 4rem;}
h1 {font-size:1.9rem!important;letter-spacing:-.04em;color:var(--navy)!important;padding-bottom:.45rem!important;}
h2 {font-size:1.35rem!important;color:var(--navy)!important;}
h3 {font-size:1.08rem!important;color:var(--navy)!important;}
p,label,[data-testid="stWidgetLabel"] p {color:var(--ink);}
[data-testid="stCaptionContainer"] p {color:var(--muted)!important;font-size:.8rem;line-height:1.6;}
[data-testid="stVerticalBlockBorderWrapper"]>div {border-color:var(--line)!important;border-radius:8px!important;}
[data-testid="stVerticalBlockBorderWrapper"] {background:#fff;border-radius:8px;}
[data-testid="stMetricLabel"] p {font-size:.8rem;color:var(--muted);}
[data-testid="stMetricValue"] {font-size:2rem;color:var(--navy);font-weight:650;}
button {border-radius:6px!important;min-height:40px;font-weight:550!important;}
button[kind="primary"],button[data-testid*="primary"],button[data-testid*="Primary"] {background:var(--navy)!important;color:#fff!important;border-color:var(--navy)!important;}
button[kind="primary"] p,button[data-testid*="primary"] p,button[data-testid*="Primary"] p {color:#fff!important;}
button[kind="secondary"],button[data-testid*="secondary"] {background:#fff;color:var(--navy);border-color:var(--line);}
button:disabled {opacity:.55!important;}
input,textarea,[role="combobox"],[data-baseweb="select"]>div {background:#fff!important;color:var(--ink)!important;border-color:#b8c8d3!important;}
[data-testid="stTextInput"]>div,[data-testid="stTextArea"]>div {background:#fff!important;}
[role="listbox"],[role="option"] {background:#fff!important;color:var(--ink)!important;}
[data-testid="stDataFrame"] {border:1px solid var(--line);border-radius:7px;overflow:hidden;}
button:focus-visible,input:focus-visible,textarea:focus-visible,a:focus-visible,[role="combobox"]:focus-visible {outline:3px solid #087e83!important;outline-offset:3px;}
[data-testid="stForm"] {background:#fff;border-color:var(--line);border-radius:8px;}
hr {border-color:var(--line);}
@media(max-width:767px) {.block-container {padding:1.2rem 1rem 3rem;}h1 {font-size:1.6rem!important;}}
</style>"""
