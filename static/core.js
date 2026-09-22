/* Appointments — core: state, API, router, components. Pages register into PAGES; actions into A. */
const $=s=>document.querySelector(s);const $$=s=>Array.from(document.querySelectorAll(s));
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money=n=>(n<0?'-':'')+'$'+(Math.abs(Math.round(n*100))/100).toFixed(2);const pad=n=>String(n).padStart(2,'0');const uid=()=>Math.random().toString(36).slice(2,8);
const now=()=>new Date();const iso=d=>d.getFullYear()+'-'+pad(d.getMonth()+1)+'-'+pad(d.getDate());const TODAY=iso(now());
const parse=s=>{const [y,m,d]=s.split('-').map(Number);return new Date(y,m-1,d)};const addD=(d,n)=>{const x=new Date(d);x.setDate(x.getDate()+n);return x};
const dow=d=>(d.getDay()+6)%7;const DAYS=['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];const MON=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const fmtT=t=>{const [h,m]=t.split(':').map(Number);return ((h+11)%12+1)+(m?':'+pad(m):'')+(h>=12?' PM':' AM')};const mins=t=>+t.slice(0,2)*60+ +t.slice(3);const tstr=m=>pad(Math.floor(m/60)%24)+':'+pad(m%60);
const nowMins=()=>now().getHours()*60+now().getMinutes();const rel=s=>s===TODAY?'Today':s===iso(addD(now(),1))?'Tomorrow':s===iso(addD(now(),-1))?'Yesterday':DAYS[dow(parse(s))]+' '+parse(s).getDate()+' '+MON[parse(s).getMonth()];
const fmtD=s=>{const d=parse(s);return DAYS[dow(d)]+' '+d.getDate()+' '+MON[d.getMonth()]};
const initials=n=>(n||'?').split(' ').map(x=>x[0]).join('').slice(0,2).toUpperCase();const sum=(a,f=x=>x)=>a.reduce((s,x)=>s+f(x),0);
const COLORS=['#2f5bea','#7c3aed','#db2777','#059669','#d97706','#0891b2'];

/* ---- state ---- */
let S=null;let R='desk';let M=null;const UI={sched:TODAY,view:'day',bk:{},cliQ:'',cliF:'all',onb:0,me:null,salesTab:'today'};
const V=()=>S.vocab;const C=id=>S.clients.find(c=>c.id===id),ST=id=>S.staff.find(s=>s.id===id),SV=id=>S.services.find(v=>v.id===id),CL=id=>S.classes.find(c=>c.id===id),PS=id=>S.passes.find(p=>p.id===id);
function activePass(c,svc){return (c.passes||[]).find(x=>x.expires>=TODAY&&(x.status||'active')==='active'&&(x.remaining===null||x.remaining>0)&&(!svc||!PS(x.passId)||!PS(x.passId).svc||PS(x.passId).svc===svc.name))}
function hoursWorked(st){let m=sum(st.shifts||[],x=>mins(x[1])-mins(x[0]));if(st.clockIn)m+=Math.max(0,nowMins()-mins(st.clockIn));return m/60}
function endOf(a){const sv=SV(a.serviceId);return tstr(mins(a.time)+(sv?sv.dur:30)+sum(a.addons||[],x=>+x.min||0))}
function blocksFor(sid,d){return (S.blocks||[]).filter(b=>b.date===d&&(!b.staffId||b.staffId===sid))}
function staffBusy(sid,date,t,dur,ex){const s=mins(t),e=s+dur;return S.appts.some(a=>a.id!==ex&&a.staffId===sid&&a.date===date&&!['cancelled','noshow'].includes(a.status)&&s<mins(a.time)+(SV(a.serviceId)?SV(a.serviceId).dur+(SV(a.serviceId).gap||0):30)+sum(a.addons||[],x=>+x.min||0)&&mins(a.time)<e)||S.classes.some(c=>c.staffId===sid&&c.days.includes(dow(parse(date)))&&s<mins(c.time)+c.dur&&mins(c.time)<e)||blocksFor(sid,date).some(b=>s<mins(b.end)&&mins(b.start)<e)}
function slots(svc,sid,date){const st=ST(sid);if(!st)return [];const h=st.hours[dow(parse(date))];if(!h)return [];const out=[];for(let m=mins(h[0]);m+svc.dur<=mins(h[1]);m+=S.rules.slot){const t=tstr(m);if(date===TODAY&&m<nowMins()+30)continue;if(!staffBusy(sid,date,t,svc.dur+(svc.gap||0)))out.push(t)}return out}
const can=lvl=>{const me=UI.me;if(!me)return true;const rank={owner:3,desk:2,staff:1};return rank[me.level||'staff']>=rank[lvl]};

/* ---- api ---- */
const LOC_KEY='hl-appt-loc';function curLoc(){try{return localStorage.getItem(LOC_KEY)}catch(e){return null}}function setLoc(id){try{localStorage.setItem(LOC_KEY,id)}catch(e){}}
async function api(method,path,body){const r=await fetch(path,{method,headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});const j=await r.json().catch(()=>({}));if(!r.ok)throw new Error(j.error||('HTTP '+r.status));return j}
const L=p=>'/api/locations/'+S.id+p;
function apply(res){S=res&&res.state?res.state:res;render()}
async function load(quiet){try{const locs=await api('GET','/api/locations');let id=curLoc();if(!locs.some(l=>l.id===id)){id=locs[0].id;setLoc(id)}S=await api('GET','/api/locations/'+id+'/state');try{const me=JSON.parse(localStorage.getItem('hl-appt-me-'+id)||'null');UI.me=me&&S.staff.some(s=>s.id===me.id)?me:null}catch(e){UI.me=null}render()}catch(e){if(!quiet)$('#app').innerHTML='<div class="empty" style="margin-top:20vh"><b>Cannot reach the API</b>'+esc(e.message)+'. Is <code>python3 server.py</code> running?</div>'}}
async function run(fn){try{await fn()}catch(e){toast(e.message)}}

/* ---- ui plumbing ---- */
function go(p){R=p;M=null;render();window.scrollTo(0,0)}
function toast(m,undo){const t=document.createElement('div');t.className='toast';t.innerHTML=esc(m)+(undo?'<button data-a="'+undo.a+'" '+Object.entries(undo.data||{}).map(([k,v])=>'data-'+k+'="'+esc(v)+'"').join(' ')+'>'+esc(undo.label||'Undo')+'</button>':'');$('#toasts').appendChild(t);setTimeout(()=>t.remove(),undo?6000:2800)}
function modal(fn,opts={}){M={fn,opts};drawM()}function closeM(){M=null;drawM()}
function drawM(){const ov=$('#ov');if(!M){ov.innerHTML='';return}ov.innerHTML='<div class="ov '+(M.opts.drawer?'drawer':'')+'" data-a="ovclose"><div class="md '+(M.opts.cls||'')+'">'+M.fn()+'</div></div>';const f=ov.querySelector('[autofocus]');if(f)setTimeout(()=>f.focus(),0)}
function mh(t,s,extra){return '<div class="mh"><div><h2>'+t+'</h2>'+(s?'<div class="sub">'+s+'</div>':'')+'</div><div class="row">'+(extra||'')+'<button class="x" data-a="close" aria-label="Close">✕</button></div></div>'}
function mb(html){return '<div class="mb">'+html+'</div>'}function mf(html){return '<div class="mf">'+html+'</div>'}
function confirmM(title,body,action,data,label){modal(()=>mh(title,'')+mb('<div class="muted">'+body+'</div>')+mf('<button class="btn" data-a="close">Cancel</button><button class="btn p danger" data-a="'+action+'" '+Object.entries(data||{}).map(([k,v])=>'data-'+k+'="'+esc(v)+'"').join(' ')+'>'+(label||'Confirm')+'</button>'))}
function refocus(sel){const i=$(sel);if(i){i.focus();if(i.setSelectionRange&&i.type!=='number')try{i.setSelectionRange(i.value.length,i.value.length)}catch(x){}}}
function avatar(name,color,lg){return '<span class="av '+(lg?'lg':'')+'" style="background:'+(color||COLORS[(name||'').length%COLORS.length])+'">'+initials(name)+'</span>'}
function tagStatus(a){const late=a.status==='booked'&&a.date===TODAY&&mins(a.time)<nowMins();return a.status==='arrived'?'<span class="tag green">checked in</span>':a.status==='done'?'<span class="tag">paid</span>':a.status==='noshow'?'<span class="tag red">no-show</span>':a.status==='cancelled'?'<span class="tag">cancelled</span>':late?'<span class="tag red">late</span>':'<span class="tag blue">booked</span>'}
function spark(vals,w=320,h=56){if(!vals.length)return '';const max=Math.max(...vals,1);const pts=vals.map((v,i)=>[(i/(vals.length-1||1))*w,h-4-(v/max)*(h-10)]);const d='M'+pts.map(p=>p[0].toFixed(1)+','+p[1].toFixed(1)).join(' L');return '<svg class="spark" viewBox="0 0 '+w+' '+h+'" preserveAspectRatio="none"><path d="'+d+' L'+w+','+h+' L0,'+h+' Z" fill="var(--accent-soft)"/><path d="'+d+'" fill="none" stroke="var(--accent)" stroke-width="1.5"/></svg>'}
function bars(items,fmt=x=>x){const max=Math.max(...items.map(x=>x[1]),1);return '<div class="stack" style="gap:6px">'+items.map(([k,v])=>'<div><div class="row between small"><span class="ell">'+esc(k)+'</span><span class="num muted">'+fmt(v)+'</span></div><div class="bar"><i style="width:'+(v/max*100)+'%"></i></div></div>').join('')+'</div>'}

/* ---- shell ---- */
const PAGES={};
const NAV=()=>[['desk','Front desk','⌘1'],['cal','Calendar','⌘2'],['cli','Clients','⌘3'],['sales','Sales','⌘4'],['cat','Catalog','⌘5'],['team','Team',''],['ins','Insights',''],['web','Website',''],['hl','HighLevel',''],['set','Settings','']];
const NATIVE=['Launchpad','Dashboard','Conversations','Calendars','Contacts','Payments','Marketing','Automation','Sites'];
const TITLES={desk:'Front desk',cal:'Calendar',cli:'Clients',sales:'Sales',cat:'Catalog',team:'Team',ins:'Insights',web:'Website & booking',hl:'HighLevel',set:'Settings'};
function shell(){const due=S.appts.filter(a=>a.date===TODAY&&a.status==='booked'&&mins(a.time)<=nowMins()+30).length;const me=UI.me;
 const nav='<aside class="nav"><div class="biz"><div class="logo">'+esc(initials(S.name))+'</div><div class="grow"><b class="ell">'+esc(S.name)+'</b><span>'+esc(V().label)+' · '+esc(S.city.split(',')[0])+'</span></div></div>'
  +'<div class="sec">'+esc(V().section.toLowerCase())+'</div>'+NAV().filter(([k])=>!(me&&me.level==='staff'&&['sales','ins','set','hl','cat','web'].includes(k))).map(([k,l,kb])=>'<a href="#" class="'+(R===k?'on':'')+'" data-a="go" data-p="'+k+'">'+l+(k==='desk'&&due?'<span class="count">'+due+'</span>':kb?'<span class="k">'+kb+'</span>':'')+'</a>').join('')
  +'<div class="sec">highlevel</div>'+NATIVE.map(n=>'<a class="native" href="#">'+n+'</a>').join('')
  +'<div class="foot">'+(me?'<div class="who">'+avatar(me.name,me.color)+'<div class="grow"><b class="ell">'+esc(me.name)+'</b><span>'+esc(me.level)+'</span></div><button class="btn xs ghost" data-a="signout">Sign out</button></div>':'<button class="btn sm" data-a="pin">Sign in with PIN</button>')+'<div class="xs faint">'+S.locations.map(l=>'<a href="#" data-a="type" data-k="'+l.id+'" style="display:inline;padding:0 6px 0 0;color:'+(l.id===S.id?'var(--ink)':'var(--ink3)')+'">'+esc(l.label)+'</a>').join('')+'</div></div></aside>';
 const top='<div class="topbar"><div class="crumb">'+esc(V().section.charAt(0)+V().section.slice(1).toLowerCase())+' / <b>'+TITLES[R]+'</b></div><span class="grow"></span><div class="search" data-a="palette"><span>Search clients, services, bookings</span><kbd>⌘K</kbd></div><div class="seg">'+[['open','Open'],['busy','Busy'],['closed','Closed']].map(([k,l])=>'<button class="'+(S.status===k?'on':'')+'" data-a="status" data-k="'+k+'">'+l+'</button>').join('')+'</div><button class="btn p sm" data-a="book">Book</button></div>';
 return '<div class="shell">'+nav+'<div class="main">'+top+'<div class="content">'+(PAGES[R]||PAGES.desk)()+'</div></div></div>'}
function render(){if(!S)return;if(!PAGES[R])R='desk';$('#app').innerHTML=shell();drawM();if(!S.onboarded)$('#app').insertAdjacentHTML('beforeend',onboarding());if(R==='cal'&&UI.view==='day'&&UI.sched===TODAY&&!UI.schedScrolled){UI.schedScrolled=true;setTimeout(()=>{const w=$('#calwrap');const l=$('.lane');if(w&&l)w.scrollTop=Math.max(0,(nowMins()-(+l.dataset.start))/30*48-160)},0)}}

/* ---- command palette ---- */
function palette(){const q=UI.palQ||'';const res=UI.palRes||[];const acts=[['book','Book an appointment'],['walkin','Walk-in'],['newcli','New client'],['go:cal','Open calendar'],['go:ins','Open insights'],['go:set','Settings']].filter(([k,l])=>!q||l.toLowerCase().includes(q.toLowerCase()));
 return '<input class="input" placeholder="Type a name, service or command…" data-in="palq" value="'+esc(q)+'" autofocus><div style="max-height:52vh;overflow:auto;padding:6px 0">'+res.map((r,i)=>'<div class="res '+(i===(UI.palI||0)?'on':'')+'" data-a="palgo" data-kind="'+r.kind+'" data-id="'+r.id+'"><span class="k">'+r.kind+'</span><span>'+esc(r.title)+'</span><span class="sub">'+esc(r.sub||'')+'</span></div>').join('')+(res.length?'<div class="divider"></div>':'')+acts.map(([k,l])=>'<div class="res" data-a="palact" data-k="'+k+'"><span class="k">action</span><span>'+l+'</span></div>').join('')+'</div>'}
let palTimer=null;

/* ---- pin sign-in ---- */
function pinModal(){const d=UI.pinBuf||'';return '<div class="pin"><div><h2>Who is at the desk?</h2><div class="muted small">Enter your PIN</div></div><div class="digits">'+[0,1,2,3].map(i=>'<i class="'+(d.length>i?'on':'')+'"></i>').join('')+'</div><div class="pad">'+[1,2,3,4,5,6,7,8,9].map(n=>'<button data-a="pindigit" data-k="'+n+'">'+n+'</button>').join('')+'<button data-a="pinclear">⌫</button><button data-a="pindigit" data-k="0">0</button><button data-a="close">Skip</button></div><div class="xs faint">Owner 1111 · Staff 2222 / 3333 in the demo</div></div>'}

/* ---- onboarding (4 steps) ---- */
function onboarding(){const i=UI.onb;const T=V();const FT=S.features;const line='padding:7px 0;border-bottom:1px solid var(--line2)';
 const svcRows=S.services.map(v=>'<div class="row between small" style="'+line+'"><span>'+esc(v.name)+' <span class="faint">· '+v.dur+' min · '+esc(v.cat)+'</span></span><span class="row"><b>'+(v.price?money(v.price):'Free')+'</b><button class="btn xs ghost" data-a="delsvc" data-id="'+v.id+'">✕</button></span></div>').join('');
 const svcForm='<form data-form="onbsvc" class="row wrap" style="gap:6px;margin-top:10px"><input class="input" name="name" placeholder="Add a service" required style="flex:2;min-width:150px" id="onb-svc-name"><input class="input" name="cat" placeholder="Category" style="flex:1;min-width:100px"><input class="input" name="dur" type="number" value="30" min="5" step="5" style="width:76px" title="Minutes"><input class="input" name="price" type="number" value="0" min="0" step="0.5" style="width:86px" title="Price"><button class="btn p sm" type="submit">Add</button></form>';
 const teamRows=S.staff.map(s=>'<div class="row small" style="'+line+'">'+avatar(s.name,s.color)+'<b>'+esc(s.name)+'</b><span class="muted grow">'+esc(s.role)+'</span><span class="faint xs">'+DAYS.filter((d,k)=>s.hours[k]).join(' ')+'</span><button class="btn xs ghost" data-a="delstaff" data-id="'+s.id+'">✕</button></div>').join('');
 const teamForm='<form data-form="onbstaff" class="row wrap" style="gap:6px;margin-top:10px"><input class="input" name="name" placeholder="Add a '+esc(T.staffOne)+'" required style="flex:2;min-width:150px" id="onb-staff-name"><input class="input" name="role" placeholder="Role" style="flex:1;min-width:100px"><button class="btn p sm" type="submit">Invite</button></form>';
 const tools=[['booking','Online booking page','Guests book at /book/'+S.slug],['reminders','Text reminders & receipts','Through HighLevel Conversations'],['passes',T.passWord,'Packs, memberships with billing, credits'],['tips','Tips at checkout','15 / 20 / 25% or a typed amount']].concat(T.hasClasses?[['classes','Classes with seats','Rosters, spot booking, waitlists']]:[]);
 const steps=['<h3>What kind of business?</h3><div class="types" style="margin-top:8px">'+S.locations.map(l=>'<div class="type '+(S.id===l.id?'on':'')+'" data-a="type" data-k="'+l.id+'" data-stay="1"><span>'+l.emoji+'</span>'+l.label+'</div>').join('')+'</div>',
  '<h3>Your '+T.svc.toLowerCase()+'</h3><div class="small muted">Remove what you don’t do, add what you do.</div><div style="max-height:260px;overflow:auto">'+svcRows+'</div>'+svcForm,
  '<h3>Your team</h3><div class="small muted">Each '+T.staffOne+' gets a login and a lane on the calendar.</div>'+teamRows+teamForm,
  '<h3>Tools</h3>'+tools.map(([k,l,d])=>'<div class="row" style="'+line+'"><div class="grow"><b class="small">'+l+'</b><div class="xs muted">'+d+'</div></div><button class="sw '+(FT[k]?'on':'')+'" data-a="feat" data-k="'+k+'" role="switch"></button></div>').join('')];
 return '<div class="onb"><div class="in"><h1>Welcome to '+esc(S.name)+'</h1><div class="muted">Four short steps. Everything can be changed later in Settings.</div><div class="steps">'+steps.map((_,k)=>'<i class="'+(k<=i?'on':'')+'"></i>').join('')+'</div><div class="box">'+steps[i]+'<div class="row" style="margin-top:6px">'+(i>0?'<button class="btn" data-a="onb" data-k="'+(i-1)+'">Back</button>':'')+'<span class="grow"></span><span class="xs faint">'+(i+1)+' of 4</span><button class="btn p" data-a="'+(i<3?'onb':'onbdone')+'" data-k="'+(i+1)+'">'+(i<3?'Next':'Open the front desk')+'</button></div></div><button class="btn ghost" style="margin-top:12px" data-a="onbdone">Skip for now</button></div></div>'}

/* ---- actions ---- */
const A={
 ovclose(el,e){if(e.target===el)closeM()},close(){closeM()},go(el){go(el.dataset.p)},toast(el){toast(el.dataset.m)},noop(){},
 openurl(el){window.open(el.dataset.url,'_blank','noopener')},copy(el){const s=el.dataset.text;(navigator.clipboard?navigator.clipboard.writeText(s):Promise.reject()).then(()=>toast('Copied'),()=>toast(s))},
 status(el){run(async()=>{apply(await api('PATCH',L(''),{status:el.dataset.k}));toast(S.status==='open'?'Accepting bookings':S.status==='busy'?'Online booking paused today':'Closed today')})},
 type(el){if(el.dataset.k===S.id)return;setLoc(el.dataset.k);UI.bk={};UI.bm=null;UI.onb=0;UI.me=null;UI.hl=null;UI.hlOpts=null;UI.recon=null;M=null;run(load)},
 feat(el){run(async()=>{const k=el.dataset.k;const b={features:{}};b.features[k]=!S.features[k];apply(await api('PATCH',L(''),b))})},
 onb(el){UI.onb=+el.dataset.k;render()},onbdone(){run(async()=>{apply(await api('PATCH',L(''),{onboarded:true}));UI.onb=0;go('desk');toast('Booking link is live')})},
 palette(){UI.palQ='';UI.palRes=[];UI.palI=0;modal(palette,{cls:'pal'})},
 palgo(el){closeM();const k=el.dataset.kind,id=el.dataset.id;if(k==='client')A.client({dataset:{id}});else if(k==='service'){go('cat');modal(()=>svcModal(id))}else if(k==='appointment')A.appt({dataset:{id}})},
 palact(el){closeM();const k=el.dataset.k;if(k.startsWith('go:'))go(k.slice(3));else A[k]&&A[k]({dataset:{}})},
 pin(){UI.pinBuf='';modal(pinModal)},pinclear(){UI.pinBuf=(UI.pinBuf||'').slice(0,-1);drawM()},
 pindigit(el){UI.pinBuf=(UI.pinBuf||'')+el.dataset.k;drawM();if(UI.pinBuf.length===4){const pin=UI.pinBuf;run(async()=>{try{const r=await api('POST',L('/auth/pin'),{pin});UI.me=r.staff;try{localStorage.setItem('hl-appt-me-'+S.id,JSON.stringify(r.staff))}catch(e){}closeM();render();toast('Signed in as '+r.staff.name)}catch(e){UI.pinBuf='';drawM();toast(e.message)}})}},
 signout(){UI.me=null;try{localStorage.removeItem('hl-appt-me-'+S.id)}catch(e){}render()},
};
const IN={palq(el){UI.palQ=el.value;clearTimeout(palTimer);palTimer=setTimeout(async()=>{try{const r=await api('GET',L('/search?q='+encodeURIComponent(UI.palQ)));UI.palRes=r.results;UI.palI=0;if(M)drawM();refocus('[data-in=palq]')}catch(e){}},160)}};
const CH={};const F={
 onbsvc(f,d){run(async()=>{apply(await api('POST',L('/services'),{name:d.name,cat:d.cat||'Services',dur:+d.dur||30,price:+d.price||0}));refocus('#onb-svc-name')})},
 onbstaff(f,d){run(async()=>{apply(await api('POST',L('/staff'),{name:d.name,role:d.role,rate:30,pin:String(1000+Math.floor(Math.random()*9000))}));refocus('#onb-staff-name')})}};

/* ---- events ---- */
document.addEventListener('click',e=>{const el=e.target.closest('[data-a]');if(!el)return;if(el.dataset.a==='ovclose'&&e.target!==el)return;if(el.tagName==='A')e.preventDefault();e.stopPropagation();A[el.dataset.a]?A[el.dataset.a](el,e):console.warn('no action',el.dataset.a)});
document.addEventListener('input',e=>{const el=e.target.closest('[data-in]');if(el&&IN[el.dataset.in])IN[el.dataset.in](el)});
document.addEventListener('change',e=>{const el=e.target.closest('[data-ch]');if(el&&CH[el.dataset.ch])CH[el.dataset.ch](el)});
document.addEventListener('submit',e=>{const f=e.target.closest('form[data-form]');if(!f)return;e.preventDefault();F[f.dataset.form]&&F[f.dataset.form](f,Object.fromEntries(new FormData(f).entries()))});
document.addEventListener('keydown',e=>{if(e.key==='Escape'){closeM();return}if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k'){e.preventDefault();A.palette();return}if((e.metaKey||e.ctrlKey)&&/^[1-5]$/.test(e.key)){e.preventDefault();go(['desk','cal','cli','sales','cat'][+e.key-1]);return}
 if(M&&M.opts.cls==='pal'){const res=UI.palRes||[];if(e.key==='ArrowDown'){UI.palI=Math.min(res.length-1,(UI.palI||0)+1);drawM();refocus('[data-in=palq]')}else if(e.key==='ArrowUp'){UI.palI=Math.max(0,(UI.palI||0)-1);drawM();refocus('[data-in=palq]')}else if(e.key==='Enter'&&res[UI.palI||0]){const r=res[UI.palI||0];A.palgo({dataset:{kind:r.kind,id:r.id}})}}
 if(e.key==='Enter'&&e.target.matches&&e.target.matches('form[data-form] input:not([type=checkbox])')){const f=e.target.closest('form');if(f&&f.requestSubmit){e.preventDefault();f.requestSubmit()}}});
window.addEventListener('DOMContentLoaded',()=>{load();setInterval(()=>{if(!M&&S)load(true)},60000)});
