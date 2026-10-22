'use strict';
const $=id=>document.getElementById(id), token=document.querySelector('meta[name=token]').content;
const compactViewportStyle=document.createElement('style');compactViewportStyle.textContent='@media(max-width:800px){header{flex-wrap:wrap}header>small{text-align:left;width:100%}.row{flex-wrap:wrap}.row>*{flex:1 1 180px}.toolbar select{max-width:100%}.toolbar span{width:100%;margin-left:0}}@media(max-height:680px){aside{position:static}textarea{height:64px}.status{padding:10px 12px}.empty{padding:36px 12px}}';document.head.append(compactViewportStyle);
const timingLine=document.createElement('small');timingLine.id='timings';timingLine.className='scan-timings';document.querySelector('.status').append(timingLine);
const responsiveStyle=document.createElement('style');responsiveStyle.textContent='html,body{min-width:0;min-height:100%;overflow-x:hidden}main{max-width:2648px;min-width:0}header{min-width:0;gap:16px}header>small{white-space:nowrap}.scan-timings{display:block;margin-top:6px;color:var(--muted);font-size:12px;overflow-wrap:anywhere}@media(max-width:1500px){.layout{grid-template-columns:300px minmax(0,1fr);gap:16px}main,header{padding:16px}aside{padding:16px}.preview{height:120px}.card{min-width:130px}.actions{gap:3px}.actions button{padding:2px 4px;font-size:10px;white-space:nowrap}}@media(max-width:1150px){.layout{grid-template-columns:minmax(0,1fr)}aside{position:static}textarea{height:82px}header{align-items:flex-start}header>small{white-space:normal;text-align:right}.toolbar{align-items:flex-start}.toolbar span{margin-left:0}}@media(max-height:850px){aside{top:10px;padding:14px}.status{padding:12px 16px;margin-bottom:12px}}';document.head.append(responsiveStyle);
const moveDialogStyle=document.createElement('style');moveDialogStyle.textContent='#moveDialog{width:92vw;max-width:calc(100vw - 16px)!important;height:min(860px,calc(100dvh - 24px));max-height:calc(100dvh - 24px);padding:18px;overflow:hidden}#moveDialog:not([open]){display:none}#moveDialog[open]{display:flex;flex-direction:column;gap:8px}#moveDialog h2,#moveDialog p{margin:0;flex:0 0 auto}#moveTitle{cursor:move;user-select:none;touch-action:none}#moveKeywords{max-height:100px;overflow:auto;flex:0 0 auto;margin:0}#moveKeywordActions{display:flex;gap:8px;flex-wrap:wrap;flex:0 0 auto}#moveKeywordActions button{padding:5px 8px}#moveList{flex:1 1 auto;min-height:80px;max-height:none!important;overflow:auto}#moveGrid{margin-bottom:0}#moveReport{max-height:64px;overflow:auto}#moveProgressBox{flex:0 0 auto}#moveProgress{width:100%;height:16px}.move-actions{display:flex;justify-content:flex-end;gap:8px;flex:0 0 auto;padding-top:8px;border-top:1px solid #2d3b4b}@media(max-height:620px){#moveDialog{padding:10px;gap:5px}#moveKeywords{max-height:58px}#moveList{min-height:50px}#moveReport{max-height:42px}#moveKeywordActions button{padding:3px 7px}}';document.head.append(moveDialogStyle);
if($('roots')&&$('roots').value.trim())$('roots').value='';
let offset=0,total=0,finishedKey='',polling=false,renderRevision=0,resultRevision=-1;
const selections=new Map(); let visibleGroups=[],cleanupPlan=null,cleanupBusy=false;
function updateImageMarks(){
for(const preview of document.querySelectorAll('.preview[data-group]')){
const selection=selections.get(Number(preview.dataset.group));
preview.classList.toggle('unselected-image',!!selection&&selection.keep!==Number(preview.dataset.image));
}
}
function currentSelections(){
const kind=$('kind').value,minimum=Number($('minSimilarity').value);
return [...selections.values()].filter(s=>(!kind||s.kind===kind)&&s.score*100>=minimum);
}
function selectionCount(){const count=currentSelections().length;$('selectedCount').textContent='已选 '+count+' 组'+(count<selections.size?' · 共记住 '+selections.size+' 组':'');updateImageMarks();}
async function api(path,data){const r=await fetch(path,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Token':token},body:JSON.stringify(data)});const j=await r.json();if(!r.ok)throw Error(j.error||'请求失败');return j;}
function feedback(e){$('feedback').textContent=e.message||e;}
function node(tag,text,cls,title){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;if(title)e.title=title;else if(cls==='path'||cls==='name')e.title=text;return e;}
function filterQuery(){const value=Number($('minSimilarity').value);if(!Number.isFinite(value)||value<0||value>100)throw Error('相似度必须在 0–100 之间');return 'kind='+encodeURIComponent($('kind').value)+'&min_similarity='+value;}
let loadingMore=false;
const hoverBox=node('div',undefined,'hover-preview'),hoverImage=node('img'),hoverCaption=node('div',undefined,'hover-caption');
hoverBox.hidden=true;hoverBox.setAttribute('aria-hidden','true');hoverBox.append(hoverImage,hoverCaption);document.body.append(hoverBox);
let hoverTimer,hoverTarget=null,hoverPoint={x:0,y:0};
function hideHover(){clearTimeout(hoverTimer);hoverBox.hidden=true;hoverTarget=null;hoverImage.removeAttribute('src');}
function positionHover(event){
if(event)hoverPoint={x:event.clientX,y:event.clientY};
const width=hoverBox.offsetWidth,height=hoverBox.offsetHeight,gap=18,margin=10;
let x=hoverPoint.x+gap;if(x+width>innerWidth-margin)x=hoverPoint.x-width-gap;
hoverBox.style.left=Math.max(margin,Math.min(x,innerWidth-width-margin))+'px';
hoverBox.style.top=Math.max(margin,Math.min(hoverPoint.y-height/2,innerHeight-height-margin))+'px';
}
function bindHover(preview,r){
preview.onpointerenter=event=>{
if(event.pointerType==='touch')return;
hideHover();hoverTarget=preview;positionHover(event);
hoverTimer=setTimeout(()=>{
if(hoverTarget!==preview||!preview.isConnected)return;
const width=Math.min(560,innerWidth-20),height=Math.min(560,innerHeight-80),scale=Math.min(width/r.w,height/r.h);
hoverImage.style.width=Math.max(1,Math.round(r.w*scale))+'px';hoverImage.style.height=Math.max(1,Math.round(r.h*scale))+'px';
hoverCaption.textContent=r.path.split(/[\\/]/).pop()+' · '+r.w+' × '+r.h;
hoverImage.alt=r.path;hoverImage.src='/api/thumb?id='+r.index+'&channel='+$('channel').value+'&size=1024';
hoverBox.hidden=false;positionHover();
},180);
};
preview.onpointermove=positionHover;preview.onpointerleave=hideHover;preview.onpointerdown=hideHover;
}
hoverImage.onerror=hideHover;
window.addEventListener('scroll',hideHover,true);window.addEventListener('resize',hideHover);window.addEventListener('blur',hideHover);
document.addEventListener('keydown',event=>{if(event.key==='Escape')hideHover();});
function drawGroups(groups,append=false){
hideHover();
const box=$('results');if(!append)box.replaceChildren();
if(!groups.length&&!append){const e=node('div',undefined,'empty');e.append(node('b','没有符合条件的重复或相似组'),node('div','可降低最低相似度或切换结果类型。'));box.append(e);}
for(const g of groups){const section=node('article',undefined,'group'),head=node('div',undefined,'grouphead');head.append(node('span',g.kind,'badge'),node('span',g.ids.length+' 张 · '+(g.score*100).toFixed(2)+'% 内容相似'));head.title=g.note+'；内容相似度按统一尺寸的 RGB/Alpha 平均误差计算';{
const choice=node('input');choice.type='checkbox';choice.checked=selections.has(g.id);choice.onchange=()=>{if(choice.checked)selections.set(g.id,{group:g.id,keep:g.keep??g.ids[0],kind:g.kind,score:g.score});else selections.delete(g.id);selectionCount();};
const label=node('label',undefined);label.append(choice,document.createTextNode('选择此组 · 保留最大'));head.append(label);
}section.append(head);const cards=node('div',undefined,'cards');for(const r of g.items){const card=node('div',undefined,'card'),preview=node('div',undefined,'preview'),img=node('img');img.src='/api/thumb?id='+r.index+'&channel='+$('channel').value;img.loading='lazy';img.alt=r.path;preview.title='双击用 Windows 默认程序打开原图';preview.style.cursor='zoom-in';preview.ondblclick=()=>api('/api/open-image',{id:r.index}).catch(feedback);preview.dataset.group=g.id;preview.dataset.image=r.index;preview.classList.toggle('unselected-image',selections.has(g.id)&&selections.get(g.id).keep!==r.index);preview.append(img);bindHover(preview,r);const info=node('div',undefined,'info');if(r.size===Math.max(...g.items.map(x=>x.size))){const keepLabel=node('label'),radio=node('input');radio.type='radio';radio.name='keep-'+g.id;radio.checked=r.index===(selections.get(g.id)?.keep??g.keep);radio.onchange=()=>{selections.set(g.id,{group:g.id,keep:r.index,kind:g.kind,score:g.score});selectionCount();repaint();};keepLabel.append(radio,document.createTextNode('保留 · 本组最大'));info.append(keepLabel);}info.append(node('b',r.path.split(/[\\/]/).pop(),'name'),node('div',r.w+' × '+r.h+' · '+(r.size/1024).toFixed(1)+' KB','meta','Alpha '+r.alpha.join('–')),node('div',r.path,'path'));const actions=node('div',undefined,'actions'),open=node('button','打开目录'),copy=node('button','复制路径');open.onclick=()=>api('/api/open',{id:r.index}).catch(feedback);copy.onclick=()=>navigator.clipboard.writeText(r.path).then(()=>feedback('已复制路径')).catch(feedback);actions.append(open,copy);info.append(actions);card.append(preview,info);cards.append(card);}section.append(cards);if(g.hidden)section.append(node('p','本组另有 '+g.hidden+' 张未展开，批量选择会包含完整组。','hint'));box.append(section);}
}
function moreStatus(){
$('count').textContent='已显示 '+visibleGroups.length+' / '+total+' 组';
$('page').textContent=visibleGroups.length>=total?'已显示全部结果':'向下滚动自动加载更多';
$('next').disabled=loadingMore||visibleGroups.length>=total;
$('next').textContent=loadingMore?'正在加载…':visibleGroups.length>=total?'已全部加载':'加载更多';
}
function repaint(){drawGroups(visibleGroups);moreStatus();}
async function render(append=false){
if(append&&(loadingMore||visibleGroups.length>=total))return;
const revision=++renderRevision;loadingMore=true;
if(!append){offset=0;visibleGroups=[];total=0;$('results').replaceChildren(node('div','正在读取结果…','empty'));}
moreStatus();
try{
const d=await api('/api/results?'+filterQuery()+'&offset='+(append?offset:0)+'&revision='+resultRevision);
if(revision!==renderRevision)return;
if(d.group_ids){const valid=new Set(d.group_ids),removed=new Set(d.removed_ids||[]);for(const [id,s] of selections)if(!valid.has(id)||removed.has(s.keep))selections.delete(id);}
resultRevision=d.revision??0;selectionCount();
visibleGroups=append?visibleGroups.concat(d.groups):d.groups;offset=visibleGroups.length;total=d.total;
if(d.counts){for(const option of $('kind').options){const label=option.value||'全部结果';option.textContent=label+'（'+(option.value?(d.counts[option.value]||0):d.available_total)+' 组）';}}
drawGroups(d.groups,append);$('errorList').textContent=(d.errors||[]).join('\n')||'无';
loadingMore=false;moreStatus();
moreObserver.unobserve($('next'));moreObserver.observe($('next'));
}catch(e){if(revision===renderRevision){feedback(e);loadingMore=false;moreStatus();$('page').textContent='加载失败，请点击加载更多重试';}}
}
const moreObserver=new IntersectionObserver(entries=>{if(entries.some(e=>e.isIntersecting)&&visibleGroups.length&&!loadingMore&&visibleGroups.length<total)render(true);},{rootMargin:'600px'});
$('scan').onclick=async()=>{try{feedback('');const roots=$('roots').value.split(/\r?\n/).map(s=>s.trim().replace(/^"|"$/g,'')).filter(Boolean);const options={};for(const id of ['similar','resize','ai','ai_vfx_only','alpha','refresh'])options[id]=$(id).checked;for(const id of ['distance','color','threshold'])options[id]=Number($(id).value);await api('/api/scan',{roots,options});selections.clear();selectionCount();offset=0;visibleGroups=[];total=0;++renderRevision;loadingMore=false;moreStatus();finishedKey='';$('results').replaceChildren(node('div','正在扫描，完成后显示结果。','empty'));await poll();}catch(e){feedback(e);}};
$('cancel').onclick=()=>api('/api/cancel',{}).then(()=>feedback('正在取消；当前图片读取完成后停止。')).catch(feedback);
$('folder').onclick=async()=>{try{$('folder').disabled=true;const d=await api('/api/folder',{});if(d.path)$('roots').value+=($('roots').value.trim()?'\n':'')+d.path;}catch(e){feedback(e);}finally{$('folder').disabled=false;}};
function changeFilter(){offset=0;selectionCount();cleanupPlan=null;render();}
$('kind').onchange=changeFilter;$('minSimilarity').oninput=changeFilter;$('channel').onchange=repaint;$('next').onclick=()=>render(true);
async function poll(){if(polling)return;polling=true;try{const s=await api('/api/status');$('phase').textContent=s.phase;$('progressText').textContent=s.total?s.done+' / '+s.total:s.done+' 项';$('progress').max=s.total||1;$('progress').value=s.done;$('scan').disabled=s.running;$('cancel').disabled=!s.running||s.operation==='cleanup';if(s.error)feedback(s.error);const r=s.summary;$('images').textContent=r?.images||0;$('groups').textContent=r?.groups||0;$('errors').textContent=r?.errors||0;let warnings=[];if(r?.ai_stats){const a=r.ai_stats;warnings.push('AI：'+a.provider+' · '+a.scope+' '+a.images+' 张（按内容去重），跳过 '+a.excluded+' 张。'+(a.fallback?' 显卡计算失败，已自动切换 CPU。':''));}if(r?.limited)warnings.push('相似候选已达到 50,000 组上限，请缩小目录或收紧阈值后重扫。');if(r&&!r.options.alpha)warnings.push('当前未严格比较 Alpha，透明度不同的贴图可能被列为相似。');$('warning').textContent=warnings.join('\n');const t=r?.timings;if(t){const parts=[['目录枚举','枚举'],['缓存检查','缓存'],['图像读取与特征','读图'],['外观候选','外观'],['AI检索','AI'],['内容相似度','评分']].filter(([key])=>t[key]!=null).map(([key,label])=>label+' '+t[key]+'s');if(t['解码复用']!=null)parts.push('复用解码 '+t['解码复用']+' 张');$('timings').textContent='扫描耗时：'+t['总耗时']+'s · '+parts.join(' / ')+(t['结果复用']?'（复用已有结果）':'');}else $('timings').textContent='';if(!s.running&&r){const key=r.images+':'+r.groups+':'+(r.revision??0)+':'+s.phase;if(key!==finishedKey){finishedKey=key;await render();}}}catch(e){feedback(e);}finally{polling=false;}}
poll();setInterval(poll,1000);

$('selectExact').onclick=async()=>{const query=filterQuery(),resultKey=finishedKey;try{$('selectExact').disabled=true;const d=await api('/api/select-largest?'+query);if(query!==filterQuery()||resultKey!==finishedKey)return;for(const s of d.selections)selections.set(s.group,s);cleanupPlan=null;selectionCount();repaint();feedback('已为当前筛选的全部 '+d.total+' 组选择文件最大的贴图（包含未加载组），已记住其他筛选中的选择。');}catch(e){feedback(e);}finally{$('selectExact').disabled=false;}};
$('clearSelection').onclick=()=>{for(const s of currentSelections())selections.delete(s.group);cleanupPlan=null;selectionCount();repaint();};
$('cleanup').onclick=async()=>{
if(cleanupBusy)return;
cleanupBusy=true;$('cleanup').disabled=true;cleanupPlan=null;
try{
filterQuery();cleanupPlan=await api('/api/cleanup-preview',{selections:currentSelections(),kind:$('kind').value,min_similarity:Number($('minSimilarity').value)});
if(!cleanupPlan.items.length){feedback('当前筛选没有要删除的文件');return;}
$('cleanupDialog').style.left='';$('cleanupDialog').style.top='';$('cleanupDialog').style.margin='';$('cleanupDialog').showModal();
await executeCleanup();
}catch(e){feedback(e);}finally{cleanupBusy=false;$('cleanup').disabled=false;}
};
const cleanupDialog=$('cleanupDialog'),cleanupHandle=$('cleanupTitle');let cleanupDrag=null;
function positionCleanup(left,top){
const rect=cleanupDialog.getBoundingClientRect();
cleanupDialog.style.margin='0';
cleanupDialog.style.left=Math.max(8,Math.min(left,innerWidth-rect.width-8))+'px';
cleanupDialog.style.top=Math.max(8,Math.min(top,innerHeight-rect.height-8))+'px';
}
cleanupHandle.onpointerdown=e=>{if(e.button!==0)return;const rect=cleanupDialog.getBoundingClientRect();cleanupDrag={x:e.clientX-rect.left,y:e.clientY-rect.top};positionCleanup(rect.left,rect.top);cleanupHandle.setPointerCapture(e.pointerId);e.preventDefault();};
cleanupHandle.onpointermove=e=>{if(cleanupDrag)positionCleanup(e.clientX-cleanupDrag.x,e.clientY-cleanupDrag.y);};
cleanupHandle.onpointerup=cleanupHandle.onpointercancel=cleanupHandle.onlostpointercapture=()=>{cleanupDrag=null;};
window.addEventListener('resize',()=>{if(cleanupDialog.open&&cleanupDialog.style.left){const rect=cleanupDialog.getBoundingClientRect();positionCleanup(rect.left,rect.top);}});
async function watchCleanup(planToken){
try{
const s=await api('/api/cleanup-status');
if(!cleanupBusy||cleanupPlan?.token!==planToken)return;
if(s.token===planToken){
$('cleanupProgress').max=s.total||1;$('cleanupProgress').value=s.done;
const percent=s.total?Math.floor(s.done*100/s.total):0;
$('cleanupProgressText').textContent=(s.done<s.checked?'正在核对文件 '+s.done+' / '+s.checked:'正在移入回收站 '+Math.max(0,s.done-s.checked)+' / '+s.files+' 个文件')+' · 总进度 '+percent+'%';
}
}catch(e){$('cleanupProgressText').textContent='进度暂时无法读取，正在等待执行结果…';}
if(cleanupBusy&&cleanupPlan?.token===planToken)setTimeout(()=>watchCleanup(planToken),250);
}
$('closeCleanup').onclick=()=>{$('cleanupDialog').close();cleanupPlan=null;};
$('cleanupDialog').addEventListener('cancel',e=>{if($('closeCleanup').disabled)e.preventDefault();});
async function executeCleanup(){
if(!cleanupPlan)return;
cleanupBusy=true;
$('closeCleanup').disabled=true;
$('cleanupSummary').textContent='正在核对文件并移入回收站，请稍候…';
$('cleanupProgressBox').hidden=false;$('cleanupProgress').value=0;$('cleanupProgress').max=1;$('cleanupProgressText').textContent='正在准备核对文件…';
const execution=api('/api/cleanup-execute',{token:cleanupPlan.token});watchCleanup(cleanupPlan.token);
try{
const d=await execution;cleanupPlan=null;
const message='已移入回收站 '+d.completed.length+' 个文件。'+d.errors.join('；');feedback(message);
if(d.errors.length){$('cleanupSummary').textContent=message;$('cleanupProgressText').textContent='已停止 · 已回收 '+d.completed.length+' 个文件';$('closeCleanup').textContent='关闭';}
else $('cleanupDialog').close();
if(d.completed.length){finishedKey='';++renderRevision;loadingMore=false;hideHover();await render();}
await poll();
}catch(e){
cleanupPlan=null;feedback(e);$('cleanupSummary').textContent='删除未完成：'+(e.message||e)+'。关闭后可重新查看清单。';$('cleanupProgressText').textContent='执行已停止';$('closeCleanup').textContent='关闭';
}finally{cleanupBusy=false;$('closeCleanup').disabled=false;}
}

let movePlan=null,moveBusy=false,movePreparing=false,moveSelection=null,moveOffset=0,moveLoading=false,moveRevision=0,movePriorityKeywords=[];
function moveKeywords(){return [...$('moveKeywords').querySelectorAll('input:checked')].map(input=>input.value);}
function renderMoveKeywords(){
$('moveKeywords').replaceChildren();
movePriorityKeywords=[];
for(const tag of moveSelection.keywords){const label=node('label'),input=node('input');input.type='checkbox';input.value=tag.keyword;input.checked=moveSelection.default_keywords.includes(tag.keyword);input.onchange=()=>changeMoveKeywords(input);label.append(input,document.createTextNode(tag.keyword+' ('+tag.count+')'));$('moveKeywords').append(label);}
}
function changeMoveKeywords(changed){
if(!moveSelection||moveBusy||movePreparing)return;
if(changed)movePriorityKeywords=changed.checked?[changed.value,...movePriorityKeywords.filter(k=>k!==changed.value)]:movePriorityKeywords.filter(k=>k!==changed.value);
++moveRevision;moveOffset=0;movePlan=null;moveSelection.total=0;moveSelection.bytes=0;
$('moveGrid').replaceChildren();$('moveList').scrollTop=0;$('moveMore').hidden=false;$('confirmMove').disabled=true;loadMoveImages();
}
function setMoveKeywords(values,prioritize=false){
if(!moveSelection||moveBusy||movePreparing)return;
const selected=new Set(values),inputs=[...$('moveKeywords').querySelectorAll('input')],previous=new Set(moveKeywords());
for(const input of inputs)input.checked=selected.has(input.value);
movePriorityKeywords=prioritize?inputs.filter(input=>input.checked&&!previous.has(input.value)).map(input=>input.value).reverse():[];
changeMoveKeywords();
}
$('clearMoveKeywords').onclick=()=>setMoveKeywords([]);
$('selectAllMoveKeywords').onclick=()=>setMoveKeywords([...$('moveKeywords').querySelectorAll('input')].map(input=>input.value),true);
$('defaultMoveKeywords').onclick=()=>setMoveKeywords(moveSelection?.default_keywords||[]);
function moveSummary(){
const keywords=moveKeywords();$('moveSummary').textContent='当前选择 '+moveSelection.total+' 张贴图 · '+(moveSelection.bytes/1048576).toFixed(2)+' MB · 已显示 '+moveOffset+' 张 · '+(keywords.length?'关键词：'+keywords.join(' / '):'未选择关键词')+'。来源：'+moveSelection.source;
}
async function loadMoveImages(){
if(!moveSelection||moveLoading||moveBusy||movePreparing)return;
moveLoading=true;$('moveMore').textContent='正在加载贴图…';const revision=moveRevision;
try{
const current=moveSelection,d=await api('/api/move-selection',{selection:current.selection,offset:moveOffset,keywords:moveKeywords(),priority_keywords:movePriorityKeywords});
if(current!==moveSelection||revision!==moveRevision)return;
moveSelection.total=d.total;moveSelection.bytes=d.bytes;$('confirmMove').disabled=moveBusy||!d.total;
for(const r of d.items){
const card=node('div',undefined,'move-card'),img=node('img');img.loading='lazy';img.src='/api/move-thumb?selection='+encodeURIComponent(current.selection)+'&id='+r.index;img.alt=r.source.split(/[\\/]/).pop();img.title=r.source;
img.onerror=()=>{img.alt='无法预览：'+r.source.split(/[\\/]/).pop();};
card.append(img,node('b','✓ '+r.source.split(/[\\/]/).pop(),'name',r.source),node('div',(r.size/1024).toFixed(1)+' KB · '+(moveKeywords().length?r.tags.join(' / '):r.reason),'hint'));$('moveGrid').append(card);
}
moveOffset+=d.items.length;moveSummary();$('moveMore').hidden=moveOffset>=d.total;$('moveMore').textContent=moveOffset>=d.total?'已全部加载':'向下滚动自动加载';
}catch(e){if(revision===moveRevision){$('moveSummary').textContent=e.message;$('moveMore').textContent='加载失败，稍后自动重试';setTimeout(()=>{if(moveSelection&&!moveLoading&&$('moveDialog').open)loadMoveImages();},2000);}}finally{moveLoading=false;if(revision!==moveRevision&&moveSelection&&$('moveDialog').open)loadMoveImages();}
}
const moveObserver=new IntersectionObserver(entries=>{if(entries.some(e=>e.isIntersecting))loadMoveImages();},{root:$('moveList'),rootMargin:'200px'});moveObserver.observe($('moveMore'));
const moveDialog=$('moveDialog'),moveHandle=$('moveTitle');let moveDrag=null;
function positionMove(left,top){const rect=moveDialog.getBoundingClientRect();moveDialog.style.margin='0';moveDialog.style.left=Math.max(8,Math.min(left,innerWidth-rect.width-8))+'px';moveDialog.style.top=Math.max(8,Math.min(top,innerHeight-rect.height-8))+'px';}
moveHandle.onpointerdown=e=>{if(e.button!==0)return;const rect=moveDialog.getBoundingClientRect();moveDrag={x:e.clientX-rect.left,y:e.clientY-rect.top};positionMove(rect.left,rect.top);moveHandle.setPointerCapture(e.pointerId);e.preventDefault();};
moveHandle.onpointermove=e=>{if(moveDrag)positionMove(e.clientX-moveDrag.x,e.clientY-moveDrag.y);};
moveHandle.onpointerup=moveHandle.onpointercancel=moveHandle.onlostpointercapture=()=>{moveDrag=null;};
window.addEventListener('resize',()=>{if(moveDialog.open&&moveDialog.style.left){const rect=moveDialog.getBoundingClientRect();positionMove(rect.left,rect.top);}});
$('moveSelect').onclick=async()=>{
try{
$('moveSelect').disabled=true;$('moveFeedback').textContent='正在选择所有贴图…';
const paths=$('roots').value.split(/\r?\n/).map(s=>s.trim().replace(/^"|"$/g,'')).filter(Boolean);
let source=paths.length===1?paths[0]:null;
if(!source){const d=await api('/api/folder',{});source=d.path;if(!source){$('moveFeedback').textContent='已取消选择';return;}}
moveSelection=await api('/api/move-select',{source});++moveRevision;moveOffset=0;movePlan=null;renderMoveKeywords();
$('moveGrid').replaceChildren();$('confirmMove').disabled=!moveSelection.total;$('confirmMove').textContent='移动';$('closeMove').textContent='关闭';$('closeMove').disabled=false;$('moveProgressBox').hidden=true;$('moveProgress').value=0;$('moveProgress').max=1;$('moveProgressText').textContent='';
$('moveReport').textContent='必须至少勾选一个关键词。显示目录名或文件名命中任一已勾选关键词的贴图，最近勾选的标签命中结果排在最前，也包含原先用途不明确的图；图标及材质排除规则仍生效。点击“移动”会移动当前筛选的全部贴图（包含未加载图片），保留子目录，同名跳过。';
moveSummary();$('moveMore').hidden=!moveSelection.total;$('moveMore').textContent='向下滚动自动加载';$('moveFeedback').textContent='已选择 '+moveSelection.total+' 张贴图';moveDialog.style.left='';moveDialog.style.top='';moveDialog.style.margin='';moveDialog.showModal();await loadMoveImages();
}catch(e){$('moveFeedback').textContent=e.message;}finally{$('moveSelect').disabled=moveBusy;}
};
$('closeMove').onclick=()=>{if(!moveBusy&&!movePreparing)$('moveDialog').close();};
moveDialog.addEventListener('cancel',event=>{if(moveBusy||movePreparing)event.preventDefault();});
function lockMoveKeywords(locked){for(const input of $('moveKeywords').querySelectorAll('input'))input.disabled=locked;for(const id of ['clearMoveKeywords','selectAllMoveKeywords','defaultMoveKeywords'])$(id).disabled=locked;}
function updateMoveProgress(done,total){
const progress=$('moveProgress');progress.max=Math.max(total,1);progress.value=Math.min(done,total);$('moveProgressText').textContent='已移动 '+done+' / '+total+' 张（'+(total?Math.floor(done*100/total):100)+'%）';
}
async function watchMove(){
try{
const state=await api('/api/move-status');
$('moveProgressBox').hidden=false;updateMoveProgress(state.done,state.total);$('moveFeedback').textContent='正在移动 '+state.done+' / '+state.total+' 张';if(state.running)$('moveSummary').textContent=$('moveFeedback').textContent;
if(state.running){setTimeout(watchMove,1000);return;}
const source=moveSelection?.source;
moveBusy=false;movePreparing=!!source;movePlan=null;$('moveSelect').disabled=!!source;$('confirmMove').disabled=true;$('closeMove').disabled=!!source;lockMoveKeywords(!!source);
const o=state.outcome,message='移动完成 '+o.completed+' 张；跳过 '+o.skipped+' 张。'+(o.errors.length?'遇到问题已停止：'+o.errors.join('；'):'')+(o.journal?' 日志：'+o.journal:'');
moveSelection=null;$('moveFeedback').textContent=message;$('moveSummary').textContent=message;$('closeMove').textContent='关闭';$('moveGrid').replaceChildren();$('moveMore').hidden=true;
if(source){
try{
moveSelection=await api('/api/move-select',{source});++moveRevision;moveOffset=0;renderMoveKeywords();$('moveGrid').replaceChildren();$('moveList').scrollTop=0;$('confirmMove').disabled=!moveSelection.total;$('moveMore').hidden=!moveSelection.total;$('moveMore').textContent='向下滚动自动加载';moveSummary();
movePreparing=false;$('moveSelect').disabled=false;$('closeMove').disabled=false;lockMoveKeywords(false);if(moveSelection.total)await loadMoveImages();
$('moveFeedback').textContent=message+' 来源已刷新，可继续选择标签移动（剩余 '+moveSelection.total+' 张可筛选贴图）。';
}catch(e){moveSelection=null;movePreparing=false;$('moveKeywords').replaceChildren();$('confirmMove').disabled=true;$('moveSelect').disabled=false;$('closeMove').disabled=false;lockMoveKeywords(false);$('moveSummary').textContent='移动已完成，但刷新来源目录失败：'+e.message;$('moveFeedback').textContent=message;}
}else{$('moveSelect').disabled=false;$('closeMove').disabled=false;lockMoveKeywords(false);}
selections.clear();selectionCount();visibleGroups=[];total=0;offset=0;finishedKey='';++renderRevision;loadingMore=false;hideHover();
$('results').replaceChildren(node('div','移动结束，请重新扫描更新贴图路径。','empty'));moreStatus();await poll();
}catch(e){$('moveFeedback').textContent='进度读取失败，将重试：'+e.message;setTimeout(watchMove,2000);}
}
$('confirmMove').onclick=async()=>{
if(!moveSelection||moveBusy||movePreparing)return;
try{
$('confirmMove').disabled=true;movePreparing=true;++moveRevision;$('moveSelect').disabled=true;$('closeMove').disabled=true;lockMoveKeywords(true);
const picked=await api('/api/folder',{});if(!picked.path){movePreparing=false;$('moveSelect').disabled=false;$('closeMove').disabled=false;lockMoveKeywords(false);return;}
movePlan=await api('/api/move-preview',{selection:moveSelection.selection,source:moveSelection.source,target:picked.path,keywords:moveKeywords(),priority_keywords:movePriorityKeywords});
$('moveReport').textContent='目标：'+picked.path+'；跳过 '+movePlan.skipped+' 张同名贴图。';
if(!movePlan.total){$('moveSummary').textContent='没有可移动的文件，目标位置已存在同名文件。';movePreparing=false;$('moveSelect').disabled=false;$('closeMove').disabled=false;lockMoveKeywords(false);return;}
moveBusy=true;$('moveSelect').disabled=true;$('closeMove').disabled=true;$('moveProgressBox').hidden=false;updateMoveProgress(0,movePlan.total);$('moveSummary').textContent='正在移动 0 / '+movePlan.total+' 张';
await api('/api/move-execute',{token:movePlan.token,confirm:true});movePreparing=false;watchMove();
}catch(e){movePreparing=false;moveBusy=false;$('moveSummary').textContent=e.message;lockMoveKeywords(false);$('moveSelect').disabled=false;$('closeMove').disabled=false;}finally{$('confirmMove').disabled=moveBusy||movePreparing||!moveSelection?.total;}
};
api('/api/move-status').then(s=>{if(s.running){moveBusy=true;$('moveSelect').disabled=true;$('moveDialog').showModal();$('closeMove').disabled=true;for(const input of $('moveKeywords').querySelectorAll('input'))input.disabled=true;watchMove();}}).catch(()=>{});
