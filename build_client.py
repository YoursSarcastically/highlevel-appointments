"""Rebuild static/index.html from the prototype source + actions.js."""
import sys
src=open(sys.argv[1],encoding='utf-8').read()
start=src.index('<meta charset="utf-8">\n<title>')
end=src.rindex('</script>')+len('</script>')
s=src[start:end]
def rep(old,new,count=1):
    global s
    n=s.count(old)
    assert n==count, ('count mismatch',n,count,old[:80])
    s=s.replace(old,new)
a=s.index('/* ---------- vocabulary per business type ---------- */')
b=s.index('const C=id=>S.clients.find')
s=s[:a]+"const V=()=>S.vocab;\nlet S=null;\n"+s[b:]
rep("function render(){save();$('#app')","function render(){if(!S)return;$('#app')")
rep("""Object.entries(TYPES).map(([k,t])=>'<button class="'+(S.type===k?'on':'')+'" data-a="type" data-k="'+k+'">'+t.emoji+' '+t.label+'</button>')""",
    """S.locations.map(l=>'<button class="'+(S.id===l.id?'on':'')+'" data-a="type" data-k="'+l.id+'">'+l.emoji+' '+l.label+'</button>')""")
rep("""Object.entries(TYPES).map(([k,t])=>'<div class="type '+(S.type===k?'on':'')+'" data-a="type" data-k="'+k+'" data-stay="1"><span>'+t.emoji+'</span>'+t.label+'</div>')""",
    """S.locations.map(l=>'<div class="type '+(S.id===l.id?'on':'')+'" data-a="type" data-k="'+l.id+'" data-stay="1"><span>'+l.emoji+'</span>'+l.label+'</div>')""")
assert s.count("V().biz.toLowerCase().replace(/[^a-z]/g,'')")>=4
s=s.replace("V().biz.toLowerCase().replace(/[^a-z]/g,'')","S.slug")
rep("""<button class="btn xs" data-a="toast" data-m="Link copied">Copy</button>""","""<button class="btn xs" data-a="copylink">Copy</button><a class="btn xs" href="/book/'+S.slug+'" target="_blank" rel="noopener">Open ↗</a>""")
rep("""<button class="btn" data-a="toast" data-m="Link copied">Copy link</button>""","""<button class="btn" data-a="copylink">Copy link</button>""")
rep("Clickable prototype — only '+V().section.toLowerCase().replace(/^\\w/,c=>c.toUpperCase())+' is live.","Connected to the Appointments API · SQLite")
a=s.index('/* ---------- onboarding ---------- */')
b=s.index('/* ---------- actions ---------- */')
s=s[:a]+open('onboarding.js',encoding='utf-8').read()+s[b:]
a=s.index('/* ---------- actions ---------- */')
b=s.index("document.addEventListener('click'")
s=s[:a]+open('actions.js',encoding='utf-8').read()+"\n"+s[b:]
# --- tabbed section: one sidebar entry, tabs across the top of the content card
rep("""'<div class="sec">'+V().section+'</div>'+NAV_LIVE().map(([k,i,l])=>'<a class="live '+(R===k?'on':'')+'" href="#" data-a="go" data-p="'+k+'"><span class="ic">'+i+'</span>'+l+(k==='desk'&&due?'<span class="badge">'+due+'</span>':'')+'</a>').join('')+'<div style="height:10px"></div>'""",
    """'<div class="sec">'+V().section+'</div><a class="live on" href="#" data-a="go" data-p="desk"><span class="ic">🏪</span>'+secName+(due?'<span class="badge">'+due+'</span>':'')+'</a><div style="height:10px"></div>'""")
rep("""const titles={desk:'Front desk',sched:'Schedule',cli:'Clients',cat:'Services & booking',staff:'Team',web:'Website & pages',eod:'End of day'};""",
    """const titles={desk:'Front desk',sched:'Schedule',cli:'Clients',cat:'Services & booking',staff:'Team',web:'Website & pages',eod:'End of day'};const secName=V().section.charAt(0)+V().section.slice(1).toLowerCase();const tabs='<nav class="tabs" aria-label="'+secName+'">'+NAV_LIVE().map(([k,i,l])=>'<a class="'+(R===k?'on':'')+'" href="#" data-a="go" data-p="'+k+'" '+(R===k?'aria-current="page"':'')+'><span class="ic">'+i+'</span>'+l+(k==='desk'&&due?'<span class="badge">'+due+'</span>':'')+'</a>').join('')+'</nav>';""")
rep("""<div class="content"><div class="page">'+({desk:pgDesk""","""<div class="content"><div class="page">'+tabs+({desk:pgDesk""")
rep("</style>",".tabs{display:flex;gap:2px;margin:-22px -22px 0;padding:0 10px;border-bottom:1px solid var(--line);overflow-x:auto;background:var(--card2);border-radius:14px 14px 0 0}.tabs a{display:flex;align-items:center;gap:7px;padding:12px 14px;font-size:13px;font-weight:600;color:var(--ink2);text-decoration:none;border-bottom:2px solid transparent;white-space:nowrap;margin-bottom:-1px}.tabs a:hover{color:var(--ink)}.tabs a.on{color:var(--blue);border-color:var(--blue)}.tabs a:focus-visible{outline:2px solid var(--blue);outline-offset:-2px;border-radius:6px}.tabs .ic{font-size:13px}.tabs .badge{margin-left:2px}@media (max-width:860px){.tabs{margin:-14px -14px 0}}\n</style>")
# --- Enter submits the inline forms
rep("document.addEventListener('keydown',e=>{if(e.key==='Escape')closeM()});","document.addEventListener('keydown',e=>{if(e.key==='Escape')closeM();if(e.key==='Enter'&&e.target.matches&&e.target.matches('form[data-form] input:not([type=checkbox])')){const f=e.target.closest('form');if(f&&f.requestSubmit){e.preventDefault();f.requestSubmit()}}});")
# --- clients: search + filters separate from Add
rep("const list=S.clients.filter(c=>!q||c.name.toLowerCase().includes(q)).sort((a,b)=>b.visits-a.visits);",
    "const f=UI.cliF||'all';const qd=q.replace(/[^0-9]/g,'');const list=S.clients.filter(c=>(!q||c.name.toLowerCase().includes(q)||(qd&&(c.phone||'').replace(/[^0-9]/g,'').includes(qd)))&&(f==='all'||(f==='pass'&&activePass(c))||(f==='nopass'&&!activePass(c))||(f==='new'&&!c.visits))).sort((a,b)=>b.visits-a.visits);")
rep("""<div class="row"><input class="input" style="width:220px" placeholder="Search" data-in="cliq" value="'+esc(UI.cliQ||'')+'"><button class="btn p" data-a="newcli">Add</button></div></div>'""",
    """<div class="row wrap"><input class="input" style="width:220px" placeholder="Search name or phone" data-in="cliq" value="'+esc(UI.cliQ||'')+'" id="cli-search" aria-label="Search clients"><div class="seg" role="group" aria-label="Filter">'+[['all','All'],['pass','Has pass'],['nopass','No pass'],['new','Never visited']].map(([k,l])=>'<button class="'+(f===k?'on':'')+'" data-a="clif" data-k="'+k+'">'+l+'</button>').join('')+'</div><button class="btn p" data-a="newcli">Add</button></div></div>'+(q||f!=='all'?'<div class="xs faint">'+list.length+' of '+S.clients.length+(q?' · matching “'+esc(UI.cliQ)+'”':'')+'</div>':'')""")
# --- HighLevel tab
rep("['eod','📊','End of day']];","['eod','📊','End of day'],['hl','🔗','HighLevel']];")
rep("web:'Website & pages',eod:'End of day'};","web:'Website & pages',eod:'End of day',hl:'HighLevel'};")
rep("staff:pgStaff,web:pgWeb,eod:pgEod})[R]()","staff:pgStaff,web:pgWeb,eod:pgEod,hl:pgHL})[R]()")
rep("if(!['desk','sched','cli','cat','staff','web','eod'].includes(R))R='desk';","if(!['desk','sched','cli','cat','staff','web','eod','hl'].includes(R))R='desk';")
a=s.index('/* ---------- new client modal ---------- */')
s=s[:a]+open('hl_tab.js',encoding='utf-8').read()+s[a:]
# --- HighLevel in Services & booking and Website & pages
rep("""<div class="row"><button class="btn" data-a="bkpreview">Booking page</button><button class="btn p" data-a="addsvc">Add</button></div></div>'""",
    """<div class="row wrap">'+(S.hl&&S.hl.linked?'<button class="btn" data-a="hlsvcimport" title="Bring HighLevel calendars in as services">Import HL calendars</button><button class="btn" data-a="hlsvcpush" title="Create a HighLevel calendar per service">Push to HL calendars</button>':'')+'<button class="btn" data-a="bkpreview">Booking page</button><button class="btn p" data-a="addsvc">Add</button></div></div>'""")
rep("""<span class="muted small">'+(v.price?money(v.price):'Free')+'</span><button class="sw '+(v.online?'on':'')+'" data-a="svcon" """,
    """'+(v.hlCalendarId?'<button class="tag blue" style="border:0;cursor:pointer" data-a="openurl" data-url="https://api.leadconnectorhq.com/widget/booking/'+v.hlCalendarId+'" title="Open the HighLevel booking widget for this calendar">HL calendar ↗</button>':'')+'<span class="muted small">'+(v.price?money(v.price):'Free')+'</span><button class="sw '+(v.online?'on':'')+'" data-a="svcon" """)
rep("""'<div class="xs faint">Runs on HighLevel Sites. Add more pages there; these are the ones the booking flow needs.</div>'}""",
    """(S.hl&&S.hl.linked?'<div class="card" style="margin-top:6px"><div class="row between wrap"><div><b>HighLevel Funnels & Websites</b><div class="small muted">'+(S.hlFunnel?'Website: <b>'+esc(S.hlFunnel.name)+'</b>'+(S.hlFunnel.url?' · '+esc(S.hlFunnel.url):''):'Pick the funnel or website that is this business\u2019s site.')+'</div></div><div class="row">'+(S.hlFunnel?'<button class="btn sm" data-a="hlfunnel" data-id="">Unlink</button>':'')+'<button class="btn sm p" data-a="hlfunnels">'+(UI.hlFunnels?'Refresh':'Load from HighLevel')+'</button></div></div>'+(UI.hlFunnelsLoading?'<div class="small faint" style="margin-top:8px">Loading…</div>':UI.hlFunnels?(UI.hlFunnels.length?'<div style="margin-top:8px">'+UI.hlFunnels.map(f=>'<div class="ap"><div class="row"><div class="grow"><b>'+esc(f.name)+'</b><span class="muted"> · '+esc(f.type)+' · '+f.steps.length+' page'+(f.steps.length===1?'':'s')+(f.url?' · '+esc(f.url):'')+'</span>'+(f.steps.length?'<div class="xs faint">'+f.steps.slice(0,6).map(st=>esc(st.name)+(st.url?' /'+esc(st.url.replace(/^\\//,'')):'')).join(' · ')+(f.steps.length>6?' · …':'')+'</div>':'')+'</div>'+(f.url?'<button class="btn sm" data-a="openurl" data-url="'+esc(f.url)+'">Open ↗</button>':'')+(S.hlFunnel&&S.hlFunnel.id===f.id?'<span class="tag green">website</span>':'<button class="btn sm p" data-a="hlfunnel" data-id="'+f.id+'">Use as website</button>')+'</div></div>').join('')+'</div>':'<div class="small faint" style="margin-top:8px">No funnels or websites in this sub-account yet.</div>'):'')+'</div>':'')+'<div class="xs faint">Runs on HighLevel Sites. Add more pages there; these are the ones the booking flow needs.</div>'}""")
# --- client drawer: HighLevel forms & surveys + email
rep("""return mh(esc(c.name),esc(c.phone)+' · '+c.visits+' visits · lives in Contacts')""",
    """const subs=(UI.subs||{})[id];return mh(esc(c.name),esc(c.phone)+(c.email?' · '+esc(c.email):'')+' · '+c.visits+' visits · '+(c.hlContactId?'linked to HighLevel':'lives in Contacts'))""")
rep("""<h3>Visits</h3><div class="stack" style="gap:6px">'+(hist.length?hist.slice(0,8)""",
    """'+(S.hl&&S.hl.linked?'<div class="row between" style="margin-top:4px"><h3>Forms & surveys</h3>'+(subs&&!subs.loading?(subs.intake_url?'<button class="btn xs" data-a="openurl" data-url="'+esc(subs.intake_url)+'">Open intake form ↗</button>':''):'<button class="btn xs" data-a="hlsubs" data-id="'+id+'">'+(subs&&subs.loading?'Loading…':'Load from HighLevel')+'</button>')+'</div>'+(subs&&!subs.loading?(subs.submissions.length?'<div class="stack" style="gap:6px">'+subs.submissions.slice(0,6).map(s=>'<div class="small" style="padding:8px 10px;background:var(--card2);border-radius:10px"><b>'+esc(s.name||s.kind)+'</b> <span class="faint xs">'+esc((s.when||'').slice(0,10))+'</span><div class="xs muted">'+Object.entries(s.answers||{}).slice(0,6).map(([k,v])=>esc(k)+': '+esc(typeof v==='string'?v:JSON.stringify(v))).join(' · ')+'</div></div>').join('')+'</div>':'<div class="faint small">No submissions for this contact yet.</div>'):''):'')+'<h3>Visits</h3><div class="stack" style="gap:6px">'+(hist.length?hist.slice(0,8)""")
# --- End of day: HighLevel reconciliation is the final word
rep("""<div class="xs faint">Everything else (trends, retention, marketing) is in HighLevel reporting.</div>'}""",
    """<div class="xs faint">Everything else (trends, retention, marketing) is in HighLevel reporting.</div>'+(S.hl&&S.hl.linked?(function(){if(!UI.recon&&!UI.reconLoading&&!UI.reconFailed){UI.reconLoading=true;api('GET',L('/hl/reconcile?date='+TODAY)).then(r=>{UI.recon=r;UI.reconLoading=false;if(R==='eod')render()}).catch(e=>{UI.reconLoading=false;UI.reconFailed=e.message;if(R==='eod')render()})}const rc=UI.recon;const dif=(a,b)=>{const x=(b||0)-(a||0);return x===0?'<span class="tag green">match</span>':'<span class="tag '+(x<0?'amber':'')+'">'+(x>0?'+':'')+(Number.isInteger(x)?x:money(x))+'</span>'};return '<div class="card final"><div class="row between wrap"><div><b>HighLevel reconciliation · final numbers</b><div class="small muted">HighLevel is the system of record. The desk is compared against it and any gap is shown.</div></div><button class="btn sm p" data-a="hlrecon">'+(rc?'Refresh':'Load')+'</button></div>'+(UI.reconLoading?'<div class="small faint" style="margin-top:8px">Reading HighLevel…</div>':UI.reconFailed&&!rc?'<div class="small" style="color:var(--red);margin-top:8px">'+esc(UI.reconFailed)+'</div>':rc?'<div class="kpis" style="margin-top:12px"><div class="kpi"><div class="l">Appointments in HighLevel</div><div class="v">'+rc.highlevel.appointments+'</div><div class="s">'+Object.entries(rc.highlevel.by_status).map(([k,v])=>k+' '+v).join(' · ')+'</div></div><div class="kpi"><div class="l">Collected in HighLevel</div><div class="v">'+money(rc.highlevel.transactions_sum)+'</div><div class="s">'+rc.highlevel.transactions+' transaction'+(rc.highlevel.transactions===1?'':'s')+'</div></div><div class="kpi"><div class="l">Invoices paid</div><div class="v">'+rc.highlevel.invoices_paid+'</div><div class="s">'+money(rc.highlevel.invoices_total)+' all time</div></div><div class="kpi"><div class="l">Desk appointments synced</div><div class="v">'+rc.desk.synced_appointments+'<span class="s"> / '+rc.desk.appointments+'</span></div><div class="s">'+(rc.desk.appointments-rc.desk.synced_appointments)+' not on a calendar</div></div></div><div class="scrollx"><table style="margin-top:12px"><thead><tr><th></th><th class="r">Front desk</th><th class="r">HighLevel (final)</th><th class="r">Difference</th></tr></thead><tbody><tr><td>Appointments today</td><td class="r">'+rc.desk.appointments+'</td><td class="r"><b>'+rc.highlevel.appointments+'</b></td><td class="r">'+dif(rc.desk.appointments,rc.highlevel.appointments)+'</td></tr><tr><td>Payments today</td><td class="r">'+rc.desk.sales+' · '+money(rc.desk.sales_sum)+'</td><td class="r"><b>'+rc.highlevel.transactions+' · '+money(rc.highlevel.transactions_sum)+'</b></td><td class="r">'+dif(rc.desk.sales_sum,rc.highlevel.transactions_sum)+'</td></tr></tbody></table></div><div class="xs faint" style="margin-top:8px">Desk statuses: '+Object.entries(rc.desk.by_status).map(([k,v])=>k+' '+v).join(' · ')+'. Gaps usually mean bookings made before the link, or switches that were off (Invoices records payments in HighLevel).</div>':'')+'</div>'})():'')}""")
# --- schedule redesign + auto-scroll to now
a=s.index('/* ---------- schedule ---------- */'); z=s.index('/* ---------- catalog ---------- */')
s=s[:a]+open('sched.js',encoding='utf-8').read()+s[z:]
rep("function render(){if(!S)return;$('#app').innerHTML=shell();drawM();","function render(){if(!S)return;$('#app').innerHTML=shell();drawM();if(R==='sched'&&UI.sched===TODAY&&!UI.schedScrolled){UI.schedScrolled=true;setTimeout(()=>A.tonow&&A.tonow(),0)}")
# --- simpler chrome: no MVP button in the top bar, no toast-y note in the sidebar
rep("""<button class="btn" data-a="mvp">What\\'s in the MVP?</button></div>""","""</div>""")
rep("""<div class="note">Connected to the Appointments API · SQLite</div></aside>""","""<div class="note"></div><a class="nav-mvp" href="#" data-a="mvp" style="display:block;margin-top:auto;padding:10px;font-size:12px;color:var(--nav-mute);text-decoration:none">About this section</a></aside>""")
# --- demo controls: seed more data
rep("""<button class="btn p" data-a="rush">Simulate '+T.rush+'</button>""","""<button class="btn p" data-a="rush">Simulate '+T.rush+'</button><button class="btn" data-a="seedmore">Add a week of demo data</button>""")
# --- tool switches gate the UI
rep(".onb .in{max-width:560px;",".onb .in{max-width:680px;")
rep("const cls=S.classes.filter(c=>c.days.includes(dow(now()))","const cls=(S.features.classes?S.classes:[]).filter(c=>c.days.includes(dow(now()))")
rep("(S.classes.length||V().hasClasses?'<div><div class=\"row\"><h3 class=\"faint grow\" style=\"margin:6px 0\">Classes</h3>","(S.features.classes&&(S.classes.length||V().hasClasses)?'<div><div class=\"row\"><h3 class=\"faint grow\" style=\"margin:6px 0\">Classes</h3>")
rep("'<div><div class=\"row\"><h3 class=\"faint grow\" style=\"margin:6px 0\">'+V().passWord+'</h3>","(!S.features.passes?'':'<div><div class=\"row\"><h3 class=\"faint grow\" style=\"margin:6px 0\">'+V().passWord+'</h3>")
rep("data-a=\"delpass\" data-id=\"'+p.id+'\">✕</button></div></div>'}).join('')+'</div>'}\nfunction bkModal(){","data-a=\"delpass\" data-id=\"'+p.id+'\">✕</button></div></div>'}).join('')+'</div>')}\nfunction bkModal(){")
rep("'</b></div><div><div class=\"small muted\" style=\"margin-bottom:6px\">Tip for '","'</b></div>'+(!S.features.tips?'':'<div><div class=\"small muted\" style=\"margin-bottom:6px\">Tip for '")
rep("data-in=\"tipamt\" value=\"'+(UI.tipAmt||'')+'\"></div></div><div class=\"row between\" style=\"font-size:18px;font-weight:800\">","data-in=\"tipamt\" value=\"'+(UI.tipAmt||'')+'\"></div></div>')+'<div class=\"row between\" style=\"font-size:18px;font-weight:800\">")
rep("(!p?'<button class=\"btn link\" data-a=\"sell\"","(!p&&S.features.passes?'<button class=\"btn link\" data-a=\"sell\"")
rep("Book</button><button class=\"btn sm\" data-a=\"sell\" data-cl=\"'+id+'\">Sell a pass</button><button class=\"btn sm\" data-a=\"toast\"","Book</button>'+(S.features.passes?'<button class=\"btn sm\" data-a=\"sell\" data-cl=\"'+id+'\">Sell a pass</button>':'')+'<button class=\"btn sm\" data-a=\"toast\"")
rep("render();setInterval(()=>{if(!M)render()},60000);","load();setInterval(()=>{if(!M&&S)load(true)},60000);")
s=s.replace('</style>','</style>\n<style>\n'+open('theme.css',encoding='utf-8').read()+'</style>',1)
s='<!doctype html><html lang="en"><head>'+s.replace('<title>Appointments MVP Prototype</title>','<title>Appointments</title>',1)
s=s.replace('<div id="app"></div>','</head><body><div id="app"></div>',1)+'\n</body></html>\n'
open('static/index.html','w',encoding='utf-8').write(s)
print('ok',len(s))
