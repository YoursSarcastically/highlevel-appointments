/* HighLevel tab actions (the tab itself is rendered by hl_tab.js → PAGES.hl) */
PAGES.hl=function(){return pgHL()};
Object.assign(A,{
 hlrefresh(){UI.hl=null;UI.hlLoading=false;UI.hlOpts=null;UI.hlOptsLoading=false;render()},
 hlsync(el){run(async()=>{const k=el.dataset.k;const body={sync:{}};body.sync[k]=!UI.hl.sync[k];UI.hl=await api('PATCH',L('/hl/settings'),body);await load(true);toast((UI.hl.sync[k]?'On: ':'Off: ')+k)})},
 hllink(el){run(async()=>{UI.hl=await api('PATCH',L('/hl/settings'),{link:el.dataset.k==='1'});await load(true);toast(el.dataset.k==='1'?S.name+' now syncs to '+(UI.hl.location?UI.hl.location.name:'HighLevel'):'Unlinked')})},
 hltest(){run(async()=>{const r=await api('POST',L('/hl/test'));toast('Connected to '+r.location.name+' · '+r.users+' users · '+r.calendars+' calendars');UI.hl=null;render()})},
 hlteam(){run(async()=>{toast('Syncing team…');const r=await api('POST',L('/hl/team'),{});S=r.state;UI.hl=null;UI.hlOpts=null;render();toast(r.report.filter(x=>x.user).length+' of '+r.report.length+' matched to users')})},
 hlpush(){run(async()=>{toast('Pushing clients…');const r=await api('POST',L('/hl/push-clients'));S=r.state;UI.hl=null;render();toast(r.pushed+' clients pushed')})},
 hlimport(){run(async()=>{toast('Importing contacts…');const r=await api('POST',L('/hl/import-contacts'));S=r.state;UI.hl=null;render();toast(r.imported+' of '+r.total+' contacts linked or created')})},
 hltx(){run(async()=>{const r=await api('GET',L('/hl/transactions'));UI.hltx=r.transactions;render()})},
 hlsvcpush(){run(async()=>{toast('Creating HighLevel calendars…');const r=await api('POST',L('/hl/services/push'));S=r.state;UI.hl=null;render();toast(r.pushed+' services now have a HighLevel calendar')})},
 hlsvcimport(){run(async()=>{const r=await api('POST',L('/hl/services/import'));S=r.state;UI.hl=null;render();toast(r.imported+' of '+r.total+' calendars linked or added')})},
 hlprod(){run(async()=>{toast('Creating products…');const r=await api('POST',L('/hl/products/push'));S=r.state;UI.hl=null;UI.hlOpts=null;render();toast(r.pushed+' products created')})},
 hllapsed(){run(async()=>{const r=await api('POST',L('/hl/email/lapsed'),{days:60});S=r.state;UI.hl=null;render();toast(r.sent+' emails sent')})},
 hlobjsetup(){run(async()=>{toast('Setting up custom objects…');const r=await api('POST',L('/hl/objects/setup'));S=r.state;UI.hl=null;UI.hlOpts=null;render();toast(Object.keys(r.objects).length+' custom objects ready')})},
 hlobjbackfill(){run(async()=>{const r=await api('POST',L('/hl/objects/backfill'));S=r.state;UI.hl=null;render();toast(r.pushed+' records written')})},
 hlseed(){run(async()=>{toast('Seeding HighLevel… this takes a minute');const r=await api('POST',L('/hl/seed'));S=r.state;UI.hl=null;UI.hlOpts=null;UI.hlSeed=r.done;render();toast('Seeded: '+Object.entries(r.done).map(([k,v])=>v+' '+k).join(' · '))})},
 hlverify(){run(async()=>{UI.hlVerifying=true;render();try{UI.hlVerify=await api('GET',L('/hl/verify'))}finally{UI.hlVerifying=false}UI.hl=null;render();toast(UI.hlVerify.ok?'Verified: everything linked exists in HighLevel':'Verified: some items are missing')})},
 hlsubs(el){run(async()=>{const id=el.dataset.id;UI.subs=UI.subs||{};UI.subs[id]={loading:true};drawM();UI.subs[id]=await api('GET',L('/hl/clients/'+id+'/submissions'));drawM()})},
});
Object.assign(CH,{
 hlfail(el){UI.hlFailOnly=el.checked;render()},
 hlwf(el){run(async()=>{const b={workflows:{}};b.workflows[el.dataset.k]=el.value;UI.hl=await api('PATCH',L('/hl/settings'),b);toast('Workflow mapping saved')})},
 hlpl(el){run(async()=>{const b={pipeline:{}};b.pipeline[el.dataset.k]=el.value;if(el.dataset.k==='id'){b.pipeline.booked='';b.pipeline.noshow='';b.pipeline.paid=''}UI.hl=await api('PATCH',L('/hl/settings'),b);render();toast('Pipeline mapping saved')})},
 hlintake(el){run(async()=>{UI.hl=await api('PATCH',L('/hl/settings'),{intake_form_id:el.value});toast(el.value?'Intake form set':'Intake form cleared')})},
});
Object.assign(F,{hlloc(f,d){run(async()=>{UI.hl=await api('PATCH',L('/hl/settings'),{location_id:d.location_id});await load(true);toast(UI.hl.location?'Connected to '+UI.hl.location.name:(UI.hl.error||'Saved'))})}});
