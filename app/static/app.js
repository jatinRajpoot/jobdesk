'use strict';
const paths = {
 grid:'<rect x="3" y="3" width="7" height="7" rx="1.4"/><rect x="14" y="3" width="7" height="7" rx="1.4"/><rect x="3" y="14" width="7" height="7" rx="1.4"/><rect x="14" y="14" width="7" height="7" rx="1.4"/>',
 search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4.5 4.5"/>',
 columns:'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16M15 4v16"/>',
 file:'<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M8 13h8M8 17h5"/>',
 user:'<circle cx="12" cy="8" r="4"/><path d="M4.5 21v-2a7.5 7.5 0 0 1 15 0v2"/>',
 sliders:'<path d="M4 6h7m4 0h5M4 12h2m4 0h10M4 18h10m4 0h2"/><circle cx="13" cy="6" r="2"/><circle cx="8" cy="12" r="2"/><circle cx="16" cy="18" r="2"/>',
 arrow:'<path d="M5 12h14m-5-5 5 5-5 5"/>',
 plus:'<path d="M12 5v14M5 12h14"/>',
 check:'<path d="m5 12 4 4L19 6"/>',
 shield:'<path d="m12 3 8 3v6c0 4-8 9-8 9s-8-5-8-9V6z"/><path d="m8 12 3 3 5-6"/>',
 briefcase:'<rect x="3" y="7" width="18" height="14" rx="2"/><path d="M8 7V4h8v3M3 12a20 20 0 0 0 18 0M12 11v4"/>',
 mail:'<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 6 9 7 9-7"/>',
 pin:'<path d="M19 10c0 5-7 11-7 11S5 15 5 10a7 7 0 1 1 14 0"/><circle cx="12" cy="10" r="2"/>',
 clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
 link:'<path d="m10 13 4-4M8 16l-1 1a4 4 0 0 1-6-6l5-5a4 4 0 0 1 6 0M16 8l1-1a4 4 0 0 1 6 6l-5 5a4 4 0 0 1-6 0"/>',
 external:'<path d="M14 3h7v7M21 3l-11 11M10 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-5"/>',
 download:'<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
 copy:'<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>',
 close:'<path d="m6 6 12 12M6 18 18 6"/>',
 menu:'<path d="M4 6h16M4 12h16M4 18h16"/>',
 sparkles:'<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z"/>',
 upload:'<path d="M12 16V3m-5 5 5-5 5 5M4 16v5h16v-5"/>',
 heart:'<path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8z"/>'
};
const icon = name => `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.file}</svg>`;
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const pretty = value => String(value || '').replaceAll('_',' ').replace(/^./, c=>c.toUpperCase());
const badge = (label, type='') => `<span class="badge ${esc(type)}">${esc(label)}</span>`;
const $ = sel => document.querySelector(sel);
const main = $('#main');
const modal = $('#modal');
let state = null;
let page = 'overview';
let selected = null;
let jobTab = 'job';
let packetTab = 'resume';
let filter = 'all';
let searchText = '';
let searchResult = null;
let searchForm = {roles:'',location:'',remote:null};
let pending = {};
let connectionTests = {};
let profileDirty = false;
let packetDirty = false;

document.querySelectorAll('[data-icon]').forEach(el=>el.innerHTML=icon(el.dataset.icon));

async function api(path, options={}) {
 const headers = {'x-jobdesk':'1', ...options.headers};
 if (options.body && !(options.body instanceof FormData)) headers['Content-Type']='application/json';
 const response=await fetch('/api'+path,{...options,headers});
 let body;
 try {body=await response.json();} catch {throw new Error('The server returned an unreadable response. Please try again.');}
 if(response.status===401){window.location.assign('/login');throw new Error('Please sign in to continue.');}
 if(!response.ok){
  const detail=Array.isArray(body.detail)?body.detail.map(x=>`${x.loc.at(-1)}: ${x.msg}`).join('; '):body.detail;
  throw new Error(detail || `Request failed (${response.status}).`);
 }
 return body;
}
function toast(message,error=false){const el=document.createElement('div');el.className='toast'+(error?' error':'');el.textContent=message;$('#toasts').append(el);setTimeout(()=>el.remove(),error?10000:5500);}
function ago(iso){if(!iso)return 'Not yet';const diff=Math.max(0,Date.now()-new Date(iso).getTime());if(diff<60000)return 'Just now';if(diff<3600000)return `${Math.floor(diff/60000)}m ago`;if(diff<86400000)return `${Math.floor(diff/3600000)}h ago`;return new Date(iso).toLocaleDateString(undefined,{month:'short',day:'numeric'});}
function heading(eyebrow,title,subtitle,action=''){return `<div class="page-heading"><div><div class="eyebrow">${eyebrow}</div><h1>${title}</h1><p class="subtitle">${subtitle}</p></div>${action}</div>`;}
function btn(label,action,extra='',kind=''){return `<button class="button ${kind}" data-action="${action}" ${extra}>${label}</button>`;}
function empty(title,text,action=''){return `<div class="empty"><div class="empty-icon">${icon('briefcase')}</div><h3>${title}</h3><p>${text}</p>${action}</div>`;}
function busy(key,text){return pending[key]?`<div class="busy-banner"><span class="spinner"></span>${text}</div>`:'';}
function jobFor(id){return state.jobs.find(j=>j.id===id);}
function jobButton(job){return `data-id="${esc(job.id)}"`;}

async function refresh(renderPage=true){state=await api('/state');updateShell();if(renderPage)render();}
function updateShell(){
 if($('#cloud-signout'))$('#cloud-signout').hidden=state.deployment?.mode!=='cloud';
 $('#job-count').textContent=state.jobs.filter(j=>j.status!=='archived').length;
 $('#sidebar-name').textContent=state.profile.name||'Your workspace';
 $('#avatar').textContent=(state.profile.name||'You').split(/\s+/).map(n=>n[0]).slice(0,2).join('').toUpperCase();
 $('#connection-dot').classList.toggle('ready',state.connections.parallel.configured&&state.connections.river.configured);
}
function render(){
 if(page==='profile'&&profileDirty&&$('#profile-form'))return;
 const titles={overview:'Overview',discover:'Discover jobs',pipeline:'Application tracker',drafts:'Resume & outreach',profile:'My profile',connections:'Connections',mail:'Email applications'};
 $('#breadcrumb-current').textContent=titles[page]||'Overview';
 document.querySelectorAll('[data-nav]').forEach(el=>el.classList.toggle('active',el.dataset.nav===page));
 const renderers={overview:overviewPage,discover:discoverPage,pipeline:pipelinePage,drafts:draftsPage,profile:profilePage,connections:connectionsPage,mail:mailPage};
 main.innerHTML=(renderers[page]||overviewPage)();
 if(page==='discover') bindJobFilter();
 document.title=`${titles[page]||'Overview'} · Jobdesk`;
}
function jobCard(job,compact=false){
 const a=job.assessment;
 const verdict=a?badge(pretty(a.verdict),a.verdict):badge('Not assessed');
 const status=badge(pretty(job.status),job.status);
 if(compact)return `<article class="job-card"><h3 class="job-title">${esc(job.title)}</h3><div class="company-line">${esc(job.company)}</div><div class="job-meta"><span>${esc(job.location)}</span></div><div class="job-bottom">${status}${btn('Open '+icon('arrow'),'open-job',jobButton(job),'small')}</div></article>`;
 return `<article class="job-card"><div class="job-header"><div class="company-logo">${esc(job.company[0]?.toUpperCase()||'?')}</div><div><h3 class="job-title">${esc(job.title)}</h3><div class="company-line">${esc(job.company)}</div></div>${verdict}</div><div class="job-meta"><span>${icon('pin')}${esc(job.location)}</span><span>${icon('link')}${esc(new URL(job.url).hostname)}</span><span>${icon('clock')}Saved ${esc(ago(job.created))}</span></div><div class="job-bottom"><div class="actions">${status}${a?.fit_score!=null?`<span class="fit"><strong>${a.fit_score}%</strong> estimated fit</span>`:''}</div><div class="job-tools">${btn('View opportunity '+icon('arrow'),'open-job',jobButton(job),'small')}</div></div></article>`;
}
function overviewPage(){
 const active=state.jobs.filter(j=>j.status!=='archived');
 const corroborated=active.filter(j=>j.assessment?.verdict==='corroborated').length;
 const drafts=state.jobs.filter(j=>j.draft).length;
 const applied=state.jobs.filter(j=>['applied','interviewing','offer','rejected'].includes(j.status)).length;
 const counts=[[active.length,'Opportunities saved','briefcase','Your personal shortlist'],[corroborated,'Roles corroborated','shield','Evidence checked'],[drafts,'Application packets','file','Tailored to your experience'],[applied,'Applications recorded','mail','Submitted by you']];
 const first=state.profile.name?.split(' ')[0];
 return heading('YOUR NEXT CHAPTER',`${first?'Welcome back, '+esc(first):'Good things are ahead'}<span class="muted">.</span>`,'A little focus. The right opportunity. Your next career move.',`<a class="button primary" href="#discover">${icon('plus')}Find opportunities</a>`)+
 `<section class="hero"><div class="hero-copy"><div class="eyebrow">LESS BUSYWORK. MORE POSSIBILITY.</div><h2>Your experience deserves<br>the right opportunity.</h2><p>Discover relevant roles, look beyond the listing,<br>and put your best application forward.</p><a class="button" href="#${state.profile.resume?'discover':'profile'}">${state.profile.resume?'Explore opportunities':'Build your profile'}${icon('arrow')}</a></div><div class="hero-art" aria-hidden="true"><div class="orbit three"></div><div class="orbit two"></div><div class="orbit"></div><span class="star-art">✦</span><div class="paper-art"><div class="art-avatar"></div><div class="art-line medium"></div><div class="art-line short"></div><div class="art-line"></div><div class="art-line medium"></div><div class="art-line"></div><div class="art-line short"></div><div class="art-check"><span>✓</span>Made for your next move</div></div></div></section>`+
 `<div class="stats">${counts.map(([count,label,ico,note])=>`<div class="stat"><div class="stat-top">${label}<span class="stat-icon">${icon(ico)}</span></div><div class="stat-number">${count.toString().padStart(2,'0')}</div><div class="stat-foot"><span class="green">${icon('check').replace('<svg','<svg width="10" height="10"')}</span>${note}</div></div>`).join('')}</div>`+
 `<div class="content-grid"><div><section><div class="section-title"><h2>Your opportunity shortlist</h2><a class="button ghost" href="#discover">View all ${icon('arrow')}</a></div>${active.length?active.slice(0,3).map(j=>jobCard(j)).join(''):empty('Make room for your next opportunity','Search for a role or save a job link. Your shortlist will live here, with the evidence and application materials in one place.',`<a class="button" href="#discover">${icon('search')}Discover your first role</a>`)}</section><section class="activity"><div class="section-title"><h2>Recent activity</h2>${badge('YOUR WORKSPACE')}</div>${state.activity.length?state.activity.slice(0,4).map(a=>`<div class="activity-row">${icon(a.kind==='draft'?'file':a.kind==='search'?'search':'check')}<div><p>${esc(a.message)}</p><small>${ago(a.time)}</small></div></div>`).join(''):'<p class="blank-statement">A fresh start. Your search activity will appear here.</p>'}</section></div><aside class="overview-aside"><div class="panel"><div class="section-title"><h2>Your launch checklist</h2>${badge(`${[!!state.profile.resume,!!state.connections.parallel.configured,!!state.jobs.length].filter(Boolean).length}/3`)}</div>${[[!!state.profile.resume,'Add your experience','Save your resume and the facts that make you, you.','profile'],[!!state.connections.parallel.configured,'Connect your research tools','Bring search and source evidence into your workspace.','connections'],[!!state.jobs.length,'Find your first opportunity','Search a role or add a job you have your eye on.','discover']].map(([done,title,note,link],idx)=>`<a class="step ${done?'done':''}" href="#${link}"><span class="step-number">${done?'✓':idx+1}</span><div><strong>${title}</strong><small>${note}</small></div></a>`).join('')}</div><div class="tip-card"><div class="eyebrow">✦ A NOTE FOR YOUR SEARCH</div><h3>Quality over the<br>quick apply.</h3><p>A relevant resume and a thoughtful introduction can say more than another generic application.</p></div></aside></div>`;
}
function discoverPage(){
 const roles=searchForm.roles||state.profile.target_roles;
 const location=searchForm.location||state.profile.target_locations;
 const remote=searchForm.remote??state.profile.remote_first??false;
 return heading('EXPLORE WHAT’S NEXT','Find your kind of opportunity.','Search the web, save a job link, and build a shortlist worth your time.',btn(icon('plus')+'Add a job','add-job'))+
 `<section class="panel search-panel"><h3>A focused search starts here</h3><p>Parallel finds source pages. DeepSeek organizes individual job listings into your shortlist.</p><form id="discovery-form" class="discovery-form"><div class="field"><label for="roles">Role or keywords</label><input id="roles" name="roles" placeholder="e.g. Backend engineer, Python" required minlength="2" maxlength="200" value="${esc(roles)}"></div><div class="field"><label for="search-location">Location</label><input id="search-location" name="location" placeholder="e.g. India, worldwide" maxlength="200" value="${esc(location)}"></div><label class="checkbox"><input type="checkbox" name="remote" ${remote?'checked':''}>Remote only</label><button class="button primary" ${pending.search?'disabled':''}>${icon('search')}${pending.search?'Searching…':'Find jobs'}</button></form>${busy('search','Finding source pages and organizing individual roles. This can take a minute.')}${searchResult?`<div class="search-summary">${searchResult.added} new opportunities saved · ${searchResult.existing} already in your workspace · ${searchResult.sources} sources reviewed</div>`:''}</section>`+
 `<div class="toolbar"><div class="search-box">${icon('search')}<input id="job-filter" aria-label="Filter saved jobs" placeholder="Search your saved opportunities…" value="${esc(searchText)}"></div><select id="status-filter" aria-label="Filter by application status" class="button"><option value="all">All stages</option>${['discovered','shortlisted','drafted','applied','interviewing','offer','rejected','archived'].map(x=>`<option value="${x}" ${filter===x?'selected':''}>${pretty(x)}</option>`).join('')}</select><span class="muted" id="filtered-count"></span></div><div id="job-results"></div>`;
}
function bindJobFilter(){
 const update=()=>{const filtered=state.jobs.filter(j=>(filter==='all'||j.status===filter)&&`${j.title} ${j.company} ${j.location}`.toLowerCase().includes(searchText.toLowerCase()));$('#filtered-count').textContent=`${filtered.length} saved`;$('#job-results').innerHTML=filtered.length?filtered.map(j=>jobCard(j)).join(''):empty(state.jobs.length?'No roles match your filter':'Your shortlist starts here',state.jobs.length?'Try a different keyword or stage.':'Use the search above, import a direct job link, or paste a job description to get started.',btn(icon('link')+'Add a job link','add-job'));};
 $('#job-filter').addEventListener('input',e=>{searchText=e.target.value;update();});
 $('#status-filter').addEventListener('change',e=>{filter=e.target.value;update();});update();
}
function pipelinePage(){
 const stages=[['shortlist','Your shortlist',['discovered','shortlisted']],['drafted','In preparation',['drafted']],['applied','Applied',['applied']],['interviewing','Conversations',['interviewing','offer']]];
 return heading('ONE CLEAR PICTURE','Every application, in view.','Track your progress from first discovery to the next conversation.',`<a href="#discover" class="button">${icon('plus')}Add opportunity</a>`)+`<div class="pipeline">${stages.map(([id,title,statuses])=>{const jobs=state.jobs.filter(j=>statuses.includes(j.status));return `<section class="pipeline-column"><div class="column-title"><strong>${title}</strong><span>${jobs.length}</span></div>${jobs.length?jobs.map(j=>jobCard(j,true)).join(''):`<div class="pipeline-empty">${id==='shortlist'?'Your next opportunity starts here.':'No applications at this stage yet.'}</div>`}</section>`;}).join('')}</div><div class="notice info">Opening a job or preparing a packet doesn’t submit an application. After you apply on the employer’s site, open the job and confirm submission to update this tracker.</div>${state.jobs.some(j=>['rejected','archived'].includes(j.status))?`<h3 class="small-heading">CLOSED & ARCHIVED</h3>${state.jobs.filter(j=>['rejected','archived'].includes(j.status)).map(j=>jobCard(j,true)).join('')}`:''}`;
}
function draftsPage(){const jobs=state.jobs.filter(j=>j.draft);return heading('A MORE PERSONAL INTRODUCTION','Put your experience forward.','Tailored resumes, outreach drafts and application answers, kept together.',`<a href="#profile" class="button">${icon('user')}Edit master profile</a>`)+(jobs.length?`<div class="draft-list">${jobs.map(j=>`<article class="panel draft-card"><div class="eyebrow">APPLICATION PACKET</div><h3>${esc(j.title)}</h3><p>${esc(j.company)} · Prepared ${ago(j.draft_at)}</p><div class="draft-meta">${badge('Resume')}${badge('Outreach')}${badge('Cover letter')}</div>${btn('Review & edit packet '+icon('arrow'),'open-packet',jobButton(j),'primary')}</article>`).join('')}</div>`:empty('A great application starts with your story','Save your master resume, choose a job, and prepare a packet that connects your experience to that role.',`<a class="button primary" href="#${state.profile.resume?'discover':'profile'}">${icon(state.profile.resume?'search':'user')}${state.profile.resume?'Choose an opportunity':'Add your resume'}</a>`));}
function field(label,name,value,placeholder='',type='text'){return `<div class="field"><label for="${name}">${label}</label><input id="${name}" name="${name}" type="${type}" value="${esc(value)}" placeholder="${esc(placeholder)}" maxlength="${name==='email'?250:300}"></div>`;}
function profilePage(){const p=state.profile;return heading('THE FACTS THAT MAKE YOU, YOU','Tell your professional story.','Your master profile is the source for every tailored application.')+`<div class="profile-layout"><form id="profile-form" class="panel"><section class="form-section"><h3>The essentials</h3><p>Keep your contact details and career direction in one place.</p><div class="form-grid">${field('Full name','name',p.name,'Your name')}${field('Email address','email',p.email,'you@example.com','email')}${field('Professional headline','headline',p.headline,'e.g. Backend engineer · Python & cloud')}${field('Current location','location',p.location,'City, country')}${field('Target roles','target_roles',p.target_roles,'Roles you want to explore')}${field('Target locations','target_locations',p.target_locations,'Where you want to work')}</div><label class="checkbox"><input type="checkbox" name="remote_first" ${p.remote_first?'checked':''}>Start new searches with Remote only selected</label></section><section class="form-section"><div class="section-title"><h3>Your master resume</h3><label class="file-upload button small">${icon('upload')}Import file<input type="file" id="resume-file" accept=".pdf,.docx,.txt,.md"></label></div><p>Paste your experience below, or import a PDF, DOCX or text file. Review imported text before saving.</p><div class="field"><label for="resume">Resume text</label><textarea id="resume" class="resume-input" name="resume" maxlength="50000" placeholder="YOUR NAME&#10;Your contact information&#10;&#10;EXPERIENCE&#10;Role · Company · Dates&#10;- Your responsibilities and achievements&#10;&#10;SKILLS&#10;Your real skills, tools and qualifications">${esc(p.resume)}</textarea></div></section><section class="form-section"><h3>Preferences & application facts</h3><p>Add facts that help answer screening questions. Leave anything you’re unsure about blank.</p><div class="field"><label for="preferences">Your preferences</label><textarea id="preferences" name="preferences" maxlength="10000" placeholder="Work authorization, notice period, salary expectations, remote preference, relocation, industries you prefer…">${esc(p.preferences)}</textarea></div></section><div class="form-footer"><span>${state.deployment?.mode==='cloud'?'Saved in your private cloud workspace.':'Saved locally on this computer.'} ${pending['profile']?'Saving…':''}</span><button class="button primary" ${pending.profile?'disabled':''}>${icon('check')}Save profile</button></div></form><aside class="profile-aside"><div class="panel"><h3>Facts first. Always.</h3><div class="profile-note"><p>Your resume is a factual foundation. The application writer is instructed to:</p><ul><li>Highlight experience relevant to each role.</li><li>Keep your employers, dates and achievements accurate.</li><li>Flag missing information for your review.</li><li>Leave unknown screening answers for you.</li></ul><p>Review every generated packet before you use it. AI can still make mistakes.</p></div></div><div class="tip-card"><div class="eyebrow">✦ MAKE IT SPECIFIC</div><h3>The details do the work.</h3><p>Include real projects, tools, results and responsibilities. A richer profile gives the writer more useful material.</p></div></aside></div>`;}
function connectionsPage(){const c=state.connections;return heading('YOUR WORKSPACE, CONNECTED','A small, capable toolkit.','Research with Parallel. Write with DeepSeek. Keep credentials on the server.')+
 `<section class="panel connection-card"><div class="provider-icon parallel">∥</div><div class="provider-copy"><h3>Parallel <span class="badge ${c.parallel.configured?'corroborated':''}">${c.parallel.configured?'Key saved':'Key needed'}</span></h3><p>Discover job postings and read source pages for evidence checks.</p><div class="provider-info">Search + Extract · basic search mode</div><form id="parallel-form" class="connection-form"><div class="field"><label for="parallel-key">${c.parallel.configured?'Replace API key':'Parallel API key'}</label><div class="inline-form"><input id="parallel-key" name="key" type="password" autocomplete="off" required minlength="10" placeholder="Paste your Parallel API key"><button class="button">Save key</button></div></div></form><div class="profile-note">On Windows, saved keys are encrypted for your Windows user. They’re never returned to the browser.</div>${connectionResult('parallel')}</div><div class="connection-test">${btn(pending['test-parallel']?'Testing…':'Test connection','test-connection','data-provider="parallel" '+(pending['test-parallel']?'disabled':''))}</div></section>`+
 `<section class="panel connection-card"><div class="provider-icon">≈</div><div class="provider-copy"><h3>DeepSeek <span class="badge ${c.river.configured?'corroborated':''}">${c.river.configured?'Credential found':'Key needed'}</span></h3><p>Organize listings, assess evidence and prepare application materials.</p><div class="provider-info">${esc(c.river.model)}<br>River AI · Low reasoning · ${esc(c.river.origin)}</div>${!c.river.configured?'<div class="notice">Set RIVER_API_KEY on the server, then restart the app.</div>':''}${connectionResult('river')}</div><div class="connection-test">${btn(pending['test-river']?'Testing…':'Test connection','test-connection','data-provider="river" '+(pending['test-river']?'disabled':''))}</div></section>`+
 gmailPanel()+
 `<section class="panel"><div class="section-title"><h2>Local usage</h2><a class="button small" href="/api/export">${icon('download')}Export workspace</a></div><p class="section-description">Request counts include connection tests and failed calls. Tokens are reported by River; this is not an invoice.</p><table class="usage-table"><thead><tr><th>PROVIDER</th><th>OPERATION</th><th>CALLS</th><th>INPUT TOKENS</th><th>OUTPUT TOKENS</th></tr></thead><tbody>${state.usage.map(u=>`<tr><td>${pretty(u.provider)}</td><td>${pretty(u.operation)}</td><td>${u.calls}</td><td>${(u.input_tokens||0).toLocaleString()}</td><td>${(u.output_tokens||0).toLocaleString()}</td></tr>`).join('')||'<tr><td colspan="5">No provider calls yet.</td></tr>'}</tbody></table><div class="profile-note">Local guardrails: 150 Parallel requests and 100 model requests per UTC day. Provider billing is managed in your provider accounts.</div></section>`;
}
function connectionResult(provider){const r=connectionTests[provider];return r?`<div class="test-result ${r.error?'error':''}">${esc(r.message)} · ${ago(r.time)}</div>`:'';}

function gmailPanel(){
 const g=state.connections.email;
 return `<section class="panel connection-card"><div class="provider-icon">${icon('mail')}</div><div class="provider-copy"><h3>Bharti’s Gmail ${badge(g.connected?'Connected':g.configured?'Ready to connect':'OAuth setup needed',g.connected?'corroborated':'')}</h3><p>Send verified email applications with the tailored resume attached. Check tracked conversations daily for replies, automatic acknowledgements and bounces.</p><div class="provider-info">${esc(g.email||g.expected_email)}<br>Permissions: send email + read-only Gmail access</div>${g.last_error?`<div class="notice">${esc(g.last_error)}</div>`:''}${g.reconnect_required?'<div class="notice">Google authorization expired or was revoked. Reconnect Gmail to resume.</div>':''}<div class="detail-actions">${g.configured?`<a class="button primary" href="/auth/google/start">${icon('mail')}${g.connected?'Reconnect Gmail':'Connect Bharti’s Gmail'}</a>`:''}<label class="button file-upload">${icon('upload')}${g.configured?'Replace OAuth client JSON':'Import OAuth client JSON'}<input id="google-client-file" type="file" accept=".json,application/json"></label>${g.connected?btn('Disconnect','disconnect-gmail')+'<a class="button" href="#mail">Open email tracker '+icon('arrow')+'</a>':''}</div><p class="profile-note">OAuth callback: ${esc(g.redirect_uri)}<br>Bharti grants access on Google’s own consent page. ${state.deployment?.mode==='cloud'?'Refresh tokens are encrypted in your private cloud workspace.':'Refresh tokens are encrypted on this computer.'} In Google’s Testing mode, reconnecting is normally required after 7 days.</p></div></section>`;
}

function mailPage(){
 const g=state.connections.email,m=state.mail,s=m.settings;
 const rows=m.applications;
 const label={queued:'Queued',sending:'Sending',sent:'Sent · awaiting reply',replied:'Reply received',automatic_reply:'Automatic acknowledgement',bounced:'Bounced',delivery_unknown:'Send unconfirmed',needs_update:'Needs updated packet',cancelled:'Cancelled',failed:'Send failed'};
 const jobTitle=id=>jobFor(id)?.title||'Saved opportunity';
 return heading('APPLICATIONS THAT KEEP MOVING','Your email application desk.','Automatic email applications and daily reply checks, connected to Bharti’s Gmail.',`<div class="actions">${btn(icon('mail')+'Check replies','check-replies',pending.mail?'disabled':'')}${btn(icon('sparkles')+'Run applications now','run-mail',pending.mail?'disabled':'','primary')}</div>`)+
 `${!g.connected?'<div class="notice">Connect Bharti’s Gmail in <a class="light-link" href="#connections">Connections</a> to start sending and checking replies.</div>':''}${busy('mail','Running the email worker. Research and application preparation can take several minutes.')}`+
 `<section class="panel mail-settings"><div class="section-title"><h2>Daily automation</h2>${badge(s.enabled?'Applications enabled':'Applications paused',s.enabled?'corroborated':'')}</div><form id="mail-settings-form"><div class="form-grid"><div><label class="checkbox"><input name="enabled" type="checkbox" ${s.enabled?'checked':''}>Automatically send eligible email applications</label><label class="checkbox"><input name="discover_daily" type="checkbox" ${s.discover_daily?'checked':''}>Discover new matching jobs during the daily run</label></div><div class="form-grid"><div class="field"><label for="daily-mail-limit">Daily email limit</label><input id="daily-mail-limit" name="daily_limit" type="number" min="1" max="20" value="${s.daily_limit}" required></div><div class="field"><label for="minimum-fit">Minimum estimated fit (%)</label><input id="minimum-fit" name="minimum_fit_score" type="number" min="0" max="100" value="${s.minimum_fit_score}" required></div></div></div><div class="form-footer"><span>${m.last_run?`Last full run: ${ago(m.last_run.finished||m.last_run.started)} · ${esc(m.last_run.outcome||'running')}`:'First run begins after Gmail is connected.'}</span><button class="button">Save settings</button></div></form><p class="profile-note">${state.deployment?.mode==='cloud'?'The daily cloud worker runs at 9:00 AM Asia/Kolkata. Your computer can be off; daily runs and reply checks execute on Google Cloud.':'Runs approximately every 24 hours while the server is running; the Windows daily task can run the worker when the web app is closed. The computer must be awake and your Windows user signed in.'} Only corroborated roles meeting the fit threshold with an explicit email application route are sent. Website-only forms remain listed for manual completion.</p></section>`+
 `<div class="section-title"><h2>Applications & responses</h2>${badge(rows.length+' tracked')}</div>${rows.length?`<div class="panel table-scroll"><table class="usage-table mail-table"><thead><tr><th>ROLE / RECIPIENT</th><th>DELIVERY & RESPONSE</th><th>LAST CHECK</th><th>ACTION</th></tr></thead><tbody>${rows.map(a=>`<tr><td><strong>${esc(jobTitle(a.job_id))}</strong><br>${esc(a.recipient)}<br><small>${esc(a.subject)}</small></td><td>${badge(label[a.status]||pretty(a.status),a.status)}${a.reply_subject?`<p>${esc(a.reply_subject)}</p>`:''}${a.last_error?`<p class="mail-error">${esc(a.last_error)}</p>`:''}${a.sent_at?`<small>Sent ${ago(a.sent_at)}</small>`:''}</td><td>${a.last_checked?ago(a.last_checked):'Not checked yet'}</td><td><div class="actions">${btn('Open role','open-job',`data-id="${a.job_id}"`,'small')}${a.thread_id?`<a class="button small" target="_blank" rel="noopener noreferrer" href="https://mail.google.com/mail/?authuser=${encodeURIComponent(a.sender)}#all/${encodeURIComponent(a.thread_id)}">Gmail ↗</a>`:''}${['queued','needs_update','failed'].includes(a.status)?btn('Cancel','cancel-mail',`data-application="${a.id}"`,'small'):''}</div></td></tr>`).join('')}</tbody></table></div>`:empty('Your first application is next','Once Gmail is connected, the worker can research roles, prepare a packet, verify an application email address and send within your daily limit.')}<div class="notice info">“Sent” means Gmail accepted the email. Replies and bounces are tracked separately; this does not guarantee employer delivery, review or an interview. An uncertain send is checked against Sent mail before any further action.</div>`+
 `${m.checks.length?`<section class="panel"><h3>Roles needing attention</h3>${m.checks.filter(c=>c.status!=='queued').map(c=>`<div class="activity-row">${icon('file')}<div><p><strong>${esc(jobTitle(c.job_id))}</strong> · ${esc(c.message)}</p>${btn('Review role','open-job',`data-id="${c.job_id}"`,'small')}</div></div>`).join('')||'<p class="profile-note">All checked roles have an application queue entry.</p>'}</section>`:''}`;
}

function showModal(html){modal.className='modal';modal.innerHTML=html;if(!modal.open)modal.showModal();}
function modalHead(title,subtitle){return `<div class="modal-head"><div><h2 id="modal-title">${esc(title)}</h2><p>${esc(subtitle)}</p></div><button class="icon-button" data-action="close-modal" aria-label="Close dialog">${icon('close')}</button></div>`;}
function openAdd(mode='link'){
 selected=null;
 showModal(modalHead('Save an opportunity','Start with a direct job link, or add the details yourself.')+`<div class="modal-content"><div class="filter-tabs"><button data-action="add-link" class="${mode==='link'?'active':''}">Import job link</button><button data-action="add-manual" class="${mode==='manual'?'active':''}">Add manually</button></div><form id="add-job-form" data-mode="${mode}"><div class="field packet-field"><label for="job-url">Original job URL</label><input id="job-url" name="url" type="url" required maxlength="2000" placeholder="https://company.com/careers/job-title"></div>${mode==='manual'?`<div class="form-grid">${field('Job title','title','','e.g. Backend Engineer')}${field('Company','company','','Employer name')}${field('Location','job-location','','Remote, city or country')}</div><div class="field packet-field"><label for="description">Job description</label><textarea id="description" name="description" minlength="30" maxlength="30000" required rows="8" placeholder="Paste the full job description, requirements and application details…"></textarea></div>`:'<div class="notice info">Parallel reads the page and DeepSeek extracts the job details. If the page is blocked or requires a login, use Add manually.</div>'}<div id="import-progress"></div><button class="button primary" id="add-job-submit">${icon('plus')}${mode==='manual'?'Save opportunity':'Import job'}</button></form></div>`);
 if(mode==='manual'){$('#title').required=true;$('#company').required=true;}
}
function openJob(id,tab='job'){
 selected=id;jobTab=tab;packetDirty=false;
 renderJobModal();
}
function renderJobModal(){
 const j=jobFor(selected);if(!j)return;
 const isBusy=pending['job-'+j.id];
 const tabs=[['job','The opportunity'],['evidence','Evidence & fit'],['packet','Application packet']];
 const snapshot=jobTab==='packet'?j.draft:jobTab==='evidence'?j.assessment:null;
 const stale=snapshot?.profile_fingerprint&&state.profile_fingerprint&&snapshot.profile_fingerprint!==state.profile_fingerprint;
 const staleNotice=stale?'<div class="notice">Your master profile has changed since this was prepared. Regenerate the packet or refresh the evidence to use your latest profile.</div>':'';
 showModal(modalHead(j.title,`${j.company} · ${j.location}`)+`<div class="modal-content"><div class="filter-tabs modal-tabs">${tabs.map(([id,label])=>`<button data-action="job-tab" data-tab="${id}" class="${jobTab===id?'active':''}">${label}${id==='packet'&&j.draft?' ✓':''}</button>`).join('')}</div>${busy('job-'+j.id,isBusy==='assess'?'Researching the listing and checking source evidence…':isBusy==='email'?'Verifying the application email and preparing the send…':'Preparing a tailored resume, outreach and screening answers…')}${staleNotice}${isBusy?'<p class="profile-note">You can close this window and keep browsing. Results will be saved when the request finishes.</p>':jobTab==='job'?jobDetail(j):jobTab==='evidence'?evidenceDetail(j):packetDetail(j)}</div>`);
}
function jobDetail(j){return `<div class="detail-meta">${badge(pretty(j.status),j.status)}${j.assessment?badge(pretty(j.assessment.verdict),j.assessment.verdict):badge('Not assessed')}${badge(j.source)}</div><div class="detail-actions"><a class="button" href="${esc(j.url)}" target="_blank" rel="noopener noreferrer">${icon('external')}Open original job / apply</a>${state.connections.email.connected?btn(icon('mail')+'Apply by email automatically','email-apply',jobButton(j)+(pending['job-'+j.id]?' disabled':''),'primary'):'<a class="button" href="#connections" data-action="close-modal">Connect Gmail to apply by email</a>'}${btn(icon('shield')+'Check evidence','assess',jobButton(j)+(pending['job-'+j.id]?' disabled':''))}${btn(icon('sparkles')+(j.draft?'Regenerate packet':'Prepare application'),'generate',jobButton(j)+(pending['job-'+j.id]?' disabled':''),'primary')}</div><h3 class="small-heading">JOB DESCRIPTION</h3><div class="detail-body">${esc(j.description)}</div><div class="split-panel"><form id="job-status-form" data-id="${j.id}"><div class="field"><label for="job-status">Application stage</label><select id="job-status" name="status">${['discovered','shortlisted','drafted','applied','interviewing','offer','rejected','archived'].map(s=>`<option value="${s}" ${j.status===s?'selected':''}>${pretty(s)}</option>`).join('')}</select></div><label class="checkbox"><input type="checkbox" name="confirm_submitted">I have submitted this application</label><button class="button small">Update stage</button></form><form id="notes-form" data-id="${j.id}"><div class="field"><label for="job-notes">Your notes</label><textarea id="job-notes" name="notes" maxlength="10000" placeholder="Recruiter contact, next steps, reminders…">${esc(j.notes)}</textarea></div><button class="button small">Save notes</button></form></div>`;}
function evidenceDetail(j){const a=j.assessment;if(!a)return empty('Look beyond the listing','Research the source, check the employer and see which claims are supported. Evidence helps you decide; it cannot guarantee a genuine job.',btn(icon('shield')+'Research this opportunity','assess',jobButton(j)+(pending['job-'+j.id]?' disabled':''),'primary'));
 return `<div class="detail-stats"><div>${badge(pretty(a.verdict),a.verdict)}<p class="profile-note">Checked ${ago(j.assessment_at)} · AI-assisted assessment</p></div>${a.fit_score!=null?`<div class="fit"><strong>${a.fit_score}%</strong> estimated fit</div>`:''}</div><p class="assessment-summary">${esc(a.summary)}</p><div class="notice info">${esc(a.fit_reason)}</div><h3 class="small-heading">SOURCE-BACKED SIGNALS</h3>${a.signals.length?a.signals.map(s=>`<article class="source-card ${s.kind==='risk'?'risk':''}"><strong>${s.kind==='risk'?'△':'✓'} ${esc(s.claim)}</strong><blockquote>“${esc(s.quote)}”</blockquote><a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.source_title||new URL(s.url).hostname)} ↗</a></article>`).join(''):'<p class="profile-note">No exact supporting quotes were validated. Review the sources directly.</p>'}<div class="split-panel"><div><h3 class="small-heading">WHAT’S STILL UNCERTAIN</h3><ul class="check-list">${a.uncertainties.map(x=>`<li>${esc(x)}</li>`).join('')}</ul></div><div><h3 class="small-heading">BEFORE YOU APPLY</h3><ul class="check-list">${a.recommended_checks.map(x=>`<li>${esc(x)}</li>`).join('')}</ul></div></div><details><summary class="profile-note">All research sources (${a.sources.length})</summary>${a.sources.map(s=>`<p class="profile-note"><a class="light-link" href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title)}</a></p>`).join('')}</details><div class="detail-actions">${btn(icon('shield')+'Refresh evidence','assess',jobButton(j)+(pending['job-'+j.id]?' disabled':''))}</div><p class="profile-note">Research can be incomplete or outdated. A real employer can be impersonated; verify the application destination before sharing personal information.</p>`;
}
function packetDetail(j){const d=j.draft;if(!d)return empty('Make the introduction yours','Use your factual profile to create a tailored resume, concise outreach and a cover letter. You can edit everything before using it.',btn(icon('sparkles')+'Prepare application packet','generate',jobButton(j)+(pending['job-'+j.id]?' disabled':''),'primary'));
 const tabs=[['resume','Resume'],['email','Outreach'],['letter','Cover letter'],['answers','Screening & changes']];
 return `<div class="notice info">Draft for your review. Check facts, wording and missing information before sending. Save edits before exporting or opening an email.</div><div class="filter-tabs">${tabs.map(([id,label])=>`<button data-action="packet-tab" data-tab="${id}" class="${packetTab===id?'active':''}">${label}</button>`).join('')}</div><form id="packet-form" data-id="${j.id}">${packetTab==='resume'?`<div class="field packet-field"><label for="packet-resume">Tailored resume <span class="muted">Editable plain text</span></label><textarea id="packet-resume" name="resume" class="resume-editor" maxlength="30000">${esc(d.resume)}</textarea></div><div class="actions"><button class="button primary">${icon('check')}Save edits</button>${['pdf','docx','txt'].map(f=>btn(icon('download')+f.toUpperCase(),'download-resume',`data-format="${f}" data-id="${j.id}"`)).join('')}</div>`:packetTab==='email'?`<div class="field packet-field"><label for="email-recipient">Recipient email</label><input id="email-recipient" type="email" placeholder="Add a verified recruiter or hiring contact" maxlength="250"><p class="profile-note">Contact addresses are never guessed. The resume must be attached in your email app.</p></div><div class="field packet-field"><label for="email-subject">Subject</label><input id="email-subject" name="email_subject" maxlength="250" value="${esc(d.email_subject)}"></div><div class="field packet-field"><label for="email-body">Your introduction</label><textarea id="email-body" name="email_body" rows="12" maxlength="7000">${esc(d.email_body)}</textarea></div><div class="actions"><button class="button primary">${icon('check')}Save edits</button>${btn(icon('mail')+'Open in email app','compose-email',jobButton(j))}${btn(icon('copy')+'Copy email','copy-email',jobButton(j))}</div>`:packetTab==='letter'?`<div class="field packet-field"><label for="cover-letter">Cover letter</label><textarea id="cover-letter" name="cover_letter" rows="16" maxlength="10000">${esc(d.cover_letter)}</textarea></div><div class="actions"><button class="button primary">${icon('check')}Save edits</button>${btn(icon('copy')+'Copy letter','copy-letter',jobButton(j))}</div>`:`<h3 class="small-heading">WHAT CHANGED</h3><ul class="check-list">${d.changes.map(x=>`<li>${esc(x)}</li>`).join('')}</ul><h3 class="small-heading">NEEDS YOUR INPUT</h3>${d.missing_information.length?`<ul class="check-list">${d.missing_information.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`:'<p class="profile-note">No missing facts flagged. Review the packet to confirm.</p>'}<h3 class="small-heading">APPLICATION ANSWERS</h3><div class="answers">${d.answers.map(a=>`<div class="answer"><strong>${esc(a.question)}</strong><p>${esc(a.answer)}</p></div>`).join('')||'<p class="profile-note">No screening questions generated for this role.</p>'}</div>`}</form><p class="saved-draft-notice">Packet generated ${ago(j.draft_at)}. Editing your master profile does not change this saved version.</p>`;
}
async function savePacket(silent=false){const j=jobFor(selected);if(!j?.draft)return;const form=$('#packet-form');if(!form)return;const fields=Object.fromEntries(new FormData(form));const packet={...j.draft,...fields};await api(`/jobs/${j.id}/draft`,{method:'PUT',body:JSON.stringify(packet)});await refresh(false);packetDirty=false;if(!silent)toast('Packet edits saved.');}
async function runJob(id,operation){
 if(packetDirty&&!confirm('Save or discard your packet edits before regenerating. Discard edits?'))return;
 pending['job-'+id]=operation;renderJobModal();
 try{await api(`/jobs/${id}/${operation}`,{method:'POST'});await refresh();jobTab=operation==='assess'?'evidence':'packet';packetDirty=false;toast(operation==='assess'?'Research saved with sources.':'Your application packet is ready to review.');}
 catch(e){toast(e.message,true);}
 finally{delete pending['job-'+id];if(modal.open&&selected===id)renderJobModal();}
}
function closeModal(){if(packetDirty&&!confirm('Discard your unsaved packet edits?'))return;modal.close();packetDirty=false;selected=null;}

document.addEventListener('click',async e=>{
 const target=e.target.closest('[data-action]');if(!target)return;
 const action=target.dataset.action,id=target.dataset.id;
 if(target.tagName==='BUTTON')e.preventDefault();
 try{
  if(action==='logout'){await api('/logout',{method:'POST'});window.location.assign('/login');}
  if(action==='add-job')openAdd();
  if(action==='add-link')openAdd('link');
  if(action==='add-manual')openAdd('manual');
  if(action==='close-modal')closeModal();
  if(action==='open-job')openJob(id);
  if(action==='open-packet'){packetTab='resume';openJob(id,'packet');}
  if(action==='job-tab'){
   if(packetDirty&&!confirm('Discard your unsaved packet edits?'))return;
   packetDirty=false;jobTab=target.dataset.tab;renderJobModal();
  }
  if(action==='packet-tab'){
   if(packetDirty)await savePacket(true);
   packetTab=target.dataset.tab;renderJobModal();
  }
  if(action==='assess'||action==='generate')await runJob(id,action);
  if(action==='email-apply'){pending['job-'+id]='email';renderJobModal();try{const r=await api(`/jobs/${id}/email-application`,{method:'POST'});await refresh();toast('Email application: '+pretty(r.application.status));}finally{delete pending['job-'+id];if(modal.open&&selected===id)renderJobModal();}}
  if(action==='check-replies'||action==='run-mail'){pending.mail=true;render();try{const r=await api(action==='check-replies'?'/mail/check-replies':'/mail/run',{method:'POST'});toast(r.outcome==='ok'?`${r.sent||0} emails sent · ${r.responses||0} new responses`:pretty(r.outcome));}finally{delete pending.mail;await refresh();}}
  if(action==='disconnect-gmail'){await api('/connections/gmail/disconnect',{method:'POST'});await refresh();toast('Gmail disconnected; automatic applications paused.');}
  if(action==='cancel-mail'){await api(`/mail/applications/${target.dataset.application}/cancel`,{method:'POST'});await refresh();toast('Queued email cancelled.');}
  if(action==='download-resume'){await savePacket(true);window.location.assign(`/api/jobs/${id}/resume/${target.dataset.format}`);}
  if(action==='copy-email'){await savePacket(true);const d=jobFor(id).draft;await navigator.clipboard.writeText(`Subject: ${d.email_subject}\n\n${d.email_body}`);toast('Email copied.');}
  if(action==='copy-letter'){await savePacket(true);await navigator.clipboard.writeText(jobFor(id).draft.cover_letter);toast('Cover letter copied.');}
  if(action==='compose-email'){
   const recipient=$('#email-recipient');if(!recipient.value.trim()||!recipient.reportValidity()){recipient.focus();throw new Error('Enter a valid recipient email address first.');}
   await savePacket(true);const d=jobFor(id).draft;
   window.location.href=`mailto:${encodeURIComponent(recipient.value.trim())}?subject=${encodeURIComponent(d.email_subject)}&body=${encodeURIComponent(d.email_body)}`;
   toast('Opening your email app. Attach the exported resume before sending.');
  }
  if(action==='test-connection'){
   const provider=target.dataset.provider;pending['test-'+provider]=true;render();
   try{const r=await api(`/connections/${provider}/test`,{method:'POST'});connectionTests[provider]={message:r.message,time:r.checked_at};toast(r.message);}
   catch(e){connectionTests[provider]={message:e.message,time:new Date().toISOString(),error:true};toast(e.message,true);}
   finally{delete pending['test-'+provider];await refresh();}
  }
 }catch(err){toast(err.message,true);}
});

document.addEventListener('submit',async e=>{
 const form=e.target;if(!form.id)return;e.preventDefault();
 try{
  if(form.id==='profile-form'){
   const fields=new FormData(form);const body={...Object.fromEntries(fields),remote_first:fields.has('remote_first')};const submit=form.querySelector('button[type="submit"], .form-footer button');submit.disabled=true;
   try{await api('/profile',{method:'PUT',body:JSON.stringify(body)});profileDirty=false;await refresh();toast('Master profile saved.');}finally{submit.disabled=false;}
  }
  if(form.id==='discovery-form'){
   const f=new FormData(form);searchForm={roles:f.get('roles').trim(),location:f.get('location').trim(),remote:f.has('remote')};pending.search=true;render();
   try{searchResult=await api('/discover',{method:'POST',body:JSON.stringify(searchForm)});await refresh(false);toast(searchResult.added?`${searchResult.added} opportunities added to your shortlist.`:'No new individual roles found. Try a more specific search or import a direct job link.');}
   finally{delete pending.search;render();}
  }
  if(form.id==='add-job-form'){
   const body=Object.fromEntries(new FormData(form));if(body['job-location']!==undefined){body.location=body['job-location']||'Not specified';delete body['job-location'];}
   const submit=$('#add-job-submit');submit.disabled=true;$('#import-progress').innerHTML='<div class="busy-banner"><span class="spinner"></span>Saving the opportunity…</div>';
   try{const r=await api(form.dataset.mode==='manual'?'/jobs':'/jobs/import',{method:'POST',body:JSON.stringify(body)});await refresh();openJob(r.job.id);toast(r.created?'Opportunity added to your shortlist.':'This opportunity is already saved.');}
   finally{submit.disabled=false;const progress=$('#import-progress');if(progress)progress.innerHTML='';}
  }
  if(form.id==='parallel-form'){
   const input=$('#parallel-key');const key=input.value;const submit=form.querySelector('button');submit.disabled=true;
   try{await api('/connections/parallel',{method:'PUT',body:JSON.stringify({key})});input.value='';delete connectionTests.parallel;await refresh();toast('Key saved. Run Test connection to validate it.');}
   finally{submit.disabled=false;}
  }
  if(form.id==='job-status-form'){
   const f=new FormData(form);await api(`/jobs/${form.dataset.id}`,{method:'PATCH',body:JSON.stringify({status:f.get('status'),confirm_submitted:f.has('confirm_submitted')})});await refresh();renderJobModal();toast('Application stage updated.');
  }
  if(form.id==='notes-form'){
   await api(`/jobs/${form.dataset.id}`,{method:'PATCH',body:JSON.stringify({notes:new FormData(form).get('notes')})});await refresh(false);toast('Notes saved.');
  }
  if(form.id==='packet-form')await savePacket();
  if(form.id==='mail-settings-form'){const f=new FormData(form);await api('/mail/settings',{method:'PUT',body:JSON.stringify({enabled:f.has('enabled'),discover_daily:f.has('discover_daily'),daily_limit:Number(f.get('daily_limit')),minimum_fit_score:Number(f.get('minimum_fit_score'))})});await refresh();toast('Email automation settings saved.');}
 }catch(err){toast(err.message,true);}
});

document.addEventListener('change',async e=>{
 if(e.target.id==='google-client-file'){const file=e.target.files[0];if(!file)return;const data=new FormData();data.append('file',file);try{await api('/connections/google/client',{method:'POST',body:data});await refresh();toast('Google OAuth client saved securely. Connect Bharti’s Gmail next.');}catch(err){toast(err.message,true);}finally{e.target.value='';}return;}
 if(e.target.id!=='resume-file')return;
 const file=e.target.files[0];if(!file)return;
 const upload=new FormData();upload.append('file',file);
 try{const r=await api('/profile/import',{method:'POST',body:upload});$('#resume').value=r.text;profileDirty=true;toast(r.truncated?'Imported the first 50,000 characters. Review and save your profile.':'Resume text imported. Review it, then save your profile.');}
 catch(err){toast(err.message,true);}
 finally{e.target.value='';}
});
document.addEventListener('input',e=>{if(e.target.closest('#profile-form'))profileDirty=true;if(e.target.closest('#packet-form')&&e.target.name)packetDirty=true;});
$('#mobile-menu').addEventListener('click',()=>$('.sidebar').classList.toggle('open'));
modal.addEventListener('cancel',e=>{e.preventDefault();closeModal();});
modal.addEventListener('click',e=>{if(e.target===modal){const r=modal.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)closeModal();}});
window.addEventListener('beforeunload',e=>{if(profileDirty||packetDirty){e.preventDefault();e.returnValue='';}});
window.addEventListener('hashchange',()=>{
 const next=location.hash.slice(1).split('?')[0]||'overview';
 if(profileDirty&&page==='profile'&&!confirm('Discard your unsaved profile changes?')){history.replaceState(null,'','#profile');return;}
 profileDirty=false;page=next;$('.sidebar').classList.remove('open');if(state)render();
});
(async()=>{try{page=location.hash.slice(1).split('?')[0]||'overview';await refresh();const oauth=new URLSearchParams(location.hash.split('?')[1]||'').get('gmail');if(oauth==='connected')toast('Bharti’s Gmail is connected. Daily applications and reply checks are ready.');if(oauth==='failed')toast('Gmail was not connected. Confirm the correct account, permissions and OAuth configuration, then try again.',true);}catch(err){main.innerHTML=`<div class="error-screen"><h2>Your workspace couldn’t load.</h2><p>${esc(err.message)}</p><p>Make sure the local server is running, then reload this page.</p></div>`;}})();
