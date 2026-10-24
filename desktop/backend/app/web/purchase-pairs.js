/* Local-only layout assistance. SKU equality/stock accounting NEVER use name similarity. */
(function(){'use strict';
const e=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),n=v=>v==null?'待核实':String(Math.round(Number(v)*1e6)/1e6);
const colors={good:'#a5d6a7',diff:'#fff176',extra:'#e0e0e0',unknown:'#e0e0e0',neutral:'#ffffff'};
function normalize(r){const known=v=>v!==null&&v!==undefined&&String(v).trim()!==''&&Number.isFinite(Number(v));const delta=known(r.plan)&&known(r.recv)?Number(r.recv)-Number(r.plan):null;const kind=r.extra||r.kind==='extra'?'extra':r.kind==='neutral'?'neutral':delta===null||r.kind==='unknown'?'unknown':Math.abs(delta)<1e-6?'good':'diff';return {...r,kind,delta};}
function canonicalText(value){return String(value||'').normalize('NFKC').toLowerCase().replace(/千克|公斤/g,'kg').replace(/毫升/g,'ml').replace(/升/g,'l').replace(/克/g,'g').replace(/洗发露|洗发乳/g,'洗发水').replace(/润发乳|护发乳|润发露/g,'护发素').replace(/洁面乳|洁面膏|洗面奶/g,'洁面').replace(/沐浴乳/g,'沐浴露').replace(/\s+/g,'').replace(/(\d+(?:\.\d+)?)(kg|ml|g|l)(?![a-z])/g,(_,n,u)=>String(Number(n)*(u==='kg'||u==='l'?1000:1))+(u==='kg'?'g':u==='l'?'ml':u));}
const variant=t=>t.replace(/蓝色|红色|粉色|黄色|绿色|紫色|白色|黑色|橙色/g,'');
// Chinese / English names of the same brand. First entry is the canonical key.
// Add a line here when a brand is written two ways in 商品资料.
const BRAND_ALIASES=[
 ['loreal','巴黎欧莱雅','欧莱雅','lorealparis'],['olay','玉兰油'],['pantene','潘婷'],['headshoulders','海飞丝','hs'],
 ['rejoice','飘柔'],['vidalsassoon','沙宣','sassoon'],['dove','多芬'],['lux','力士'],['clear','清扬'],['vaseline','凡士林'],
 ['nivea','妮维雅'],['safeguard','舒肤佳'],['lg','安宝笛'],['johnsons','强生','johnson'],['biore','碧柔'],['kao','花王'],
 ['lion','狮王'],['maybelline','美宝莲'],['larocheposay','理肤泉'],['schwarzkopf','施华蔻'],['thefaceshop','菲诗小铺','faceshop'],
 ['innisfree','悦诗风吟'],['mistine','蜜丝婷'],['crest','佳洁士'],['colgate','高露洁'],['whisper','护舒宝'],['comfort','金纺'],
 ['omo','奥妙'],['carmex','卡麦克斯','小蜜缇'],['stridex','施颜适'],['kato','卡朵'],['amore','爱茉莉','amorepacific'],
 ['ryo','吕'],['freeplus','芙丽芳丝'],['ponds','旁氏'],['cerave','适乐肤'],['hazeline','夏士莲'],['syoss','丝蕴'],
 ['darlie','好来','黑人'],['shiseido','资生堂'],['jillleen','jill leen'],['jmsolution','jm']
];
const BRAND_LOOKUP=BRAND_ALIASES.flatMap(g=>g.map(a=>[a.replace(/[\p{P}\p{Z}]/gu,'').toLowerCase(),g[0]])).sort((a,b)=>b[0].length-a[0].length);
// "玉兰油OLAY面霜" and "Olay沐浴露" both become "olay…"; the alias run is consumed.
function canonicalBrand(key){let brand='',rest=key,hit=true;while(hit){hit=false;for(const [a,c] of BRAND_LOOKUP)if(rest.startsWith(a)&&(!brand||brand===c)){brand=c;rest=rest.slice(a.length);hit=true;break;}}return brand?{brand,key:brand+'|'+rest}:{brand:'',key};}
function traits(r){let name=canonicalText(r.name),spec=canonicalText(r.spec);if(!name||/^货位/.test(name)||/^(未提供商品名|未知商品|未命名)$/.test(name))name=spec;
 const meta=name+' '+spec,type=(meta.match(/护发精油|护发素|发膜|洗发水|沐浴露|洗面奶|洁面|牙膏|洗衣液|香皂|粉底|唇膏|身体乳|护手霜|眼霜|面霜|面膜|卸妆水|卸妆油|卸妆膏|爽肤水|化妆水|防晒|洗手液|漱口水|干发喷雾|留香珠|散粉|粉饼|睫毛膏|眉笔/)||[])[0]||'';
 const units=[...new Set([...meta.matchAll(/(\d+(?:\.\d+)?)(ml|g)(?![a-z])/g)].map(m=>m[0]))].sort().join(',');
 const pack=(meta.match(/\d+(?:支|瓶|袋|件|个)装/)||[])[0]||'';
 const size=(spec.match(/(?:^|[^a-z0-9])([2-6]?x{0,3}[slm]|\d{2,3}码)(?=$|[^a-z0-9])/i)||[])[1]||'';
 // Specifications often carry the actual product name; do not discard bracketed
 // content or ignore spec when every colour shares the same generic title.
 const compact=s=>s.replace(/新版|旧版|新款|旧款|新包装|老包装|正品|特价/g,'').replace(/[\p{P}\p{Z}]/gu,'');
 const words=s=>variant(s).replace(/\d+(?:\.\d+)?(?:ml|g)(?![a-z])|\d+(?:支|瓶|袋|件|个)装/g,'').replace(/(?:^|[^a-z0-9])([2-6]?x{0,3}[slm]|\d{2,3}码)(?=$|[^a-z0-9])/gi,'').replace(/[\p{P}\p{Z}\d]/gu,'');
 const descriptive=words(name+' '+spec).length>=2,descriptiveSpec=words(spec).length>=4;
 // Brand is the leading part of the product name once shelf/packaging noise is
 // removed ("（-全码）溪木源…", "@LG…", "盒装-蒂佳婷…", "日本杜得乐…").
 const lead=name.replace(/^(?:\s*(?:[(（【\[][^)）】\]]*[)）】\]]|[@#*＊\-_~·]|盒装|袋装|瓶装|进口|日本|韩国|法国|德国|美国|英国|泰国|澳洲|澳大利亚|意大利|西班牙|新西兰))+/u,'');
 const brandInfo=canonicalBrand(words(compact(lead))),brandKey=brandInfo.key,brandName=brandInfo.brand;
 name=compact(name);spec=compact(spec);const s=spec&&!name.includes(spec)?name+spec:name;
 const sku=String(r.sku_id||'').normalize('NFKC').toLowerCase().match(/^([a-z]+)[-_]?(\d+)/);
 return {s,name,spec,type,units,pack,size,descriptive,descriptiveSpec,skuPrefix:sku?sku[1]:'',skuNum:sku?Number(sku[2]):null,barcode:/^\d{12,14}$/.test(String(r.sku_code||'').trim())?String(r.sku_code).trim():'',words:words(name+' '+spec),brandKey,brandName,volumes:new Set([...meta.matchAll(/(\d+(?:\.\d+)?)(ml|g)(?![a-z])/g)].map(m=>Number(m[1])))};}
// Product families whose members a supplier commonly ships in place of each
// other (shampoo arriving as the matching conditioner). Layout only.
const FAMILY={洗发水:'hair',护发素:'hair',发膜:'hair',洁面:'face',洗面奶:'face'};
const TYPE_WORDS=/护发精油|护发素|发膜|洗发水|沐浴露|洗面奶|洁面|牙膏|洗衣液|香皂|粉底|唇膏|身体乳|护手霜|眼霜|面霜|面膜|卸妆水|卸妆油|卸妆膏|爽肤水|化妆水|防晒|洗手液|漱口水|干发喷雾|留香珠|散粉|粉饼|睫毛膏|眉笔/g;
// 70ml vs "70ml / 70G": data entry mixes ml and g, so compare the amounts.
function sameVolume(x,y){if(!x.units||!y.units)return !x.units&&!y.units?null:null;if(x.units===y.units)return true;for(const v of x.volumes)if(y.volumes.has(v))return true;return false;}
function commonPrefix(a,b){let i=0;while(i<a.length&&i<b.length&&a[i]===b[i])i++;return i;}
// Shared leading brand of at least two characters that is not just a product
// word ("洗发水…"). SKU letter prefixes and GS1 barcode prefixes are NOT brand
// evidence: one code family / one manufacturer covers several brands
// (DF = 多芬/清扬/力士; P&G 6903148 = 潘婷/海飞丝).
const GENERIC_LEAD=/^(?:洗发|护发|发膜|沐浴|洁面|洗面|牙膏|洗衣|香皂|粉底|唇膏|身体|面霜|面膜|卸妆)/;
function sharedBrand(x,y){if(x.brandName||y.brandName)return x.brandName===y.brandName?x.brandName.length+1:0;const n=commonPrefix(x.brandKey,y.brandKey);return n>=2&&!GENERIC_LEAD.test(x.brandKey.slice(0,n))?n:0;}
// Same brand + same volume + same product family: the classic "similar item"
// (another scent, the conditioner instead of the shampoo, a neighbouring code).
function familyScore(x,y){
 if(sameVolume(x,y)!==true)return 0;
 const sameType=x.type&&x.type===y.type,family=x.type&&y.type&&FAMILY[x.type]&&FAMILY[x.type]===FAMILY[y.type];
 if(!sameType&&!family)return 0;
 const brand=sharedBrand(x,y);if(!brand)return 0;
 const sameSkuFamily=x.skuPrefix&&x.skuPrefix===y.skuPrefix;
 // Same GS1 company prefix only strengthens an already shared brand.
 const sameMaker=x.barcode&&y.barcode&&x.barcode.length===y.barcode.length&&commonPrefix(x.barcode,y.barcode)>=7;
 const aliases=BRAND_LOOKUP.filter(([,c])=>c===x.brandName).map(([a])=>a);
 const strip=t=>aliases.reduce((v,a)=>v.split(a).join(''),x.brandName?t:t.replace(x.brandKey.slice(0,brand),'')).replace(TYPE_WORDS,'').replace(/(?:ml|g)/g,'');
 let score=.6+.2*dice(strip(x.words),strip(y.words));
 if(sameType)score+=.06;
 if(sameMaker)score+=.03;
 if(sameSkuFamily){score+=.03;if(x.skuNum!==null&&y.skuNum!==null&&Math.abs(x.skuNum-y.skuNum)<=3)score+=.08;}
 return Math.min(.95,score);
}
// Brand could not be confirmed (unknown or unlisted brand, name without brand).
// Same product type/family + same volume is then only a *guess*, used solely
// when the missing and extra quantities are identical. Known different brands
// (多芬 vs 力士) are never guessed.
function looseEvidence(a,b){const x=traits(a),y=traits(b);if(!x.s||!y.s)return 0;if(x.brandName&&y.brandName&&x.brandName!==y.brandName)return 0;if(sameVolume(x,y)!==true)return 0;
 const sameType=x.type&&x.type===y.type,family=x.type&&y.type&&FAMILY[x.type]&&FAMILY[x.type]===FAMILY[y.type];if(!sameType&&!family)return 0;
 if(x.pack&&y.pack&&x.pack!==y.pack||x.size&&y.size&&x.size!==y.size)return 0;
 let e=1;if(sameType)e+=1;if(x.barcode&&y.barcode&&commonPrefix(x.barcode,y.barcode)>=7)e+=1;if(x.skuPrefix&&x.skuPrefix===y.skuPrefix&&x.skuNum!==null&&y.skuNum!==null&&Math.abs(x.skuNum-y.skuNum)<=3)e+=2;return e;}
function dice(a,b){if(a===b)return 1;if(a.length<2||b.length<2)return 0;const grams=s=>{const m=new Map();for(let i=0;i<s.length-1;i++){const g=s.slice(i,i+2);m.set(g,(m.get(g)||0)+1);}return m;};const aa=grams(a),bb=grams(b);let common=0;for(const [k,v] of aa)common+=Math.min(v,bb.get(k)||0);return 2*common/(a.length+b.length-2);}
function similarity(a,b){const x=traits(a),y=traits(b);if(!x.s||!y.s||!x.descriptive||!y.descriptive)return 0;const fam=familyScore(x,y);if(x.type&&y.type&&x.type!==y.type)return fam;if(sameVolume(x,y)===false||x.pack&&y.pack&&x.pack!==y.pack||x.size&&y.size&&x.size!==y.size)return 0;
 if(x.s===y.s)return 1;const vx=variant(x.s),vy=variant(y.s);if(vx.length>=4&&vx===vy)return .98;
 // No shared leading brand: a fuzzy name overlap ("洗发水…500g") is not
 // enough to sit two brands side by side.
 if(!sharedBrand(x,y))return fam;
 let score=dice(x.s,y.s);
 // Same meaningful name with metadata present on only one side remains a
 // candidate; conflicting type/volume/pack/size was already rejected above.
 if(x.name.length>=4&&x.name===y.name)score=Math.max(score,.9);
 if(x.descriptiveSpec&&y.descriptiveSpec&&x.spec===y.spec)score=Math.max(score,.86);
 return Math.max(score,fam);
}
// Rectangular Hungarian assignment: dummy columns leave weak candidates unpaired.
function assignment(weights){const N=weights.length;if(!N)return [];const M=weights[0].length,u=Array(N+1).fill(0),v=Array(M+1).fill(0),p=Array(M+1).fill(0),way=Array(M+1).fill(0);for(let i=1;i<=N;i++){p[0]=i;let j0=0,minv=Array(M+1).fill(Infinity),used=Array(M+1).fill(false);do{used[j0]=true;const i0=p[j0];let delta=Infinity,j1=0;for(let j=1;j<=M;j++)if(!used[j]){const cur=-weights[i0-1][j-1]-u[i0]-v[j];if(cur<minv[j]){minv[j]=cur;way[j]=j0}if(minv[j]<delta){delta=minv[j];j1=j}}for(let j=0;j<=M;j++){if(used[j]){u[p[j]]+=delta;v[j]-=delta}else minv[j]-=delta}j0=j1}while(p[j0]);do{let j1=way[j0];p[j0]=p[j1];j0=j1}while(j0)}const out=Array(N).fill(-1);for(let j=1;j<=M;j++)if(p[j])out[p[j]-1]=j-1;return out;}
const overrides=new Map(),contexts=new Map();
function layoutKey(input){return JSON.stringify(input.map(r=>[r.sku_id,r.plan,r.recv,r.kind,r.name,r.spec]));}
function model(input){const rows=input.map(normalize),left=rows.filter(r=>r.kind==='diff'&&r.delta<0),right=rows.filter(r=>(r.kind==='extra'&&r.recv>0||r.kind==='diff'&&r.delta>0)),good=rows.filter(r=>r.kind==='good'),unknown=rows.filter(r=>r.kind==='unknown'),pairs=left.map(l=>({left:[l],right:[]})),used=new Set(),fixed=new Set(),manual=overrides.get(layoutKey(input))||{};
const amount=r=>r.kind==='extra'?Number(r.recv):r.delta;
// Human layout overrides affect neither quantities nor original rows. An SKU is used once.
left.forEach((l,i)=>{if(Object.prototype.hasOwnProperty.call(manual,l.sku_id)){fixed.add(i);for(const sku of manual[l.sku_id]){const j=right.findIndex(r=>r.sku_id===sku);if(j>=0&&!used.has(j)){pairs[i].right.push(right[j]);used.add(j);}}}});
// Search whole reliable combinations BEFORE one-to-one allocation. Bound work, never drop leftovers.
const options=[];
// Pairwise scores once; an extra row is not offered to a short row when another
// short row is clearly its better counterpart (keeps OLY46+OLY47 together).
const sim=left.length+right.length<=350?left.map(l=>right.map(r=>similarity(l,r))):[];
// Large reports used to skip ALL automatic pairing beyond 350 rows. Match
// canonical names in bounded buckets first instead of silently leaving them blank.
if(left.length+right.length>350){const buckets=new Map();right.forEach((r,j)=>{const k=variant(traits(r).name);if(!buckets.has(k))buckets.set(k,[]);buckets.get(k).push(j);});left.forEach((l,i)=>{if(fixed.has(i))return;const candidates=(buckets.get(variant(traits(l).name))||[]).filter(j=>!used.has(j)&&similarity(l,right[j])>=.85);if(candidates.length){const j=candidates[0];pairs[i].right=[right[j]];pairs[i].ambiguous=candidates.length>1;used.add(j);fixed.add(i);}});}
if(left.length+right.length<=350)left.forEach((l,i)=>{if(fixed.has(i))return;const candidates=right.map((r,j)=>({j,s:sim[i][j]})).filter(c=>!used.has(c.j)&&c.s>=.58&&!left.some((_,o)=>o!==i&&!fixed.has(o)&&sim[o][c.j]>c.s+.05)).sort((a,b)=>b.s-a.s||a.j-b.j).slice(0,12);let combos=[{ids:[],sum:0,score:1}];for(const c of candidates){const add=[];for(const v of combos){if(v.ids.length>=5||v.ids.some(j=>similarity(right[j],right[c.j])<.58))continue;const sum=v.sum+amount(right[c.j]);if(sum>-l.delta+1e-6)continue;add.push({ids:[...v.ids,c.j],sum,score:Math.min(v.score,c.s)});}combos.push(...add);}for(const v of combos)if(v.ids.length&&Math.abs(v.sum+l.delta)<1e-6)options.push({...v,i,quality:v.score});});
options.sort((a,b)=>b.quality-a.quality||b.sum-a.sum||b.ids.length-a.ids.length||a.i-b.i);
for(const v of options){if(fixed.has(v.i)||v.ids.some(j=>used.has(j)))continue;pairs[v.i].right=v.ids.map(j=>right[j]);pairs[v.i].ambiguous=options.some(x=>x.i!==v.i&&x.ids.some(j=>v.ids.includes(j)));v.ids.forEach(j=>used.add(j));fixed.add(v.i);}
const remaining=left.map((_,i)=>i).filter(i=>!fixed.has(i)),available=right.map((_,j)=>j).filter(j=>!used.has(j));
if(remaining.length&&available.length&&left.length+right.length<=350){const weights=remaining.map(i=>available.map(j=>{const t=similarity(left[i],right[j]);return t<.58?0:Math.round(t*1000)}).concat(Array(remaining.length).fill(0)));assignment(weights).forEach((j,k)=>{if(j>=0&&j<available.length&&weights[k][j]>0){const id=available[j];pairs[remaining[k]].right=[right[id]];pairs[remaining[k]].ambiguous=weights[k].some((w,o)=>o!==j&&o<available.length&&w>=weights[k][j]-40)||weights.some((row,o)=>o!==k&&row[j]>=weights[k][j]-40);used.add(id);}});}
right.forEach((r,j)=>{if(used.has(j))return;const fits=pairs.filter((g,i)=>!Object.prototype.hasOwnProperty.call(manual,left[i]?.sku_id)&&g.right.length&&similarity(g.left[0],r)>=.65&&g.right.every(x=>similarity(x,r)>=.58)&&g.right.reduce((s,x)=>s+amount(x),amount(r))<=-g.left[0].delta+1e-6).sort((a,b)=>similarity(b.left[0],r)-similarity(a.left[0],r));if(fits.length){fits[0].right.push(r);used.add(j);}});
// Leftover extras that are the same product in another colour/variant of an
// already paired short row still sit beside it even when the total overshoots
// (short 7: pink 3 + yellow 5). Quantities stay per-SKU; layout only.
right.forEach((r,j)=>{if(used.has(j))return;const fits=pairs.filter((g,i)=>!Object.prototype.hasOwnProperty.call(manual,left[i]?.sku_id)&&g.right.length&&similarity(g.left[0],r)>=.85&&g.right.every(x=>similarity(x,r)>=.85)).sort((a,b)=>similarity(b.left[0],r)-similarity(a.left[0],r));if(fits.length){fits[0].right.push(r);fits[0].over=true;used.add(j);}});
// Last resort for rows still alone: exact quantity + same type + same volume,
// and the candidate must be the single best on both sides.
if(left.length+right.length<=350){const open=left.map((_,i)=>i).filter(i=>!pairs[i].right.length&&!Object.prototype.hasOwnProperty.call(manual,left[i].sku_id)),free=right.map((_,j)=>j).filter(j=>!used.has(j));
 const ev=open.map(i=>free.map(j=>Math.abs(amount(right[j])+left[i].delta)<1e-6?looseEvidence(left[i],right[j]):0));
 const best=(list)=>{const m=Math.max(0,...list);return m>0&&list.filter(v=>v===m).length===1?list.indexOf(m):-1;};
 open.forEach((i,a)=>{const b=best(ev[a]);if(b<0)return;const col=ev.map(row=>row[b]);if(best(col)!==a)return;const j=free[b];if(used.has(j))return;pairs[i].right=[{...right[j],guess:true}];pairs[i].guess=true;used.add(j);});}
right.forEach((r,j)=>{if(!used.has(j))pairs.push({left:[],right:[r]});});return {pairs,good,unknown};}
function manualPanel(rows){const key=layoutKey(rows);let id=[...contexts].find(([,v])=>layoutKey(v)===key)?.[0];if(!id){id=String(contexts.size+1);contexts.set(id,rows);}const m=model(rows);return '<details class="pair-manual"><summary>相似款仅并排参考 · 歧义可手动并排 / 解除</summary><p>不会合并编码或抵消差异。先选少收商品，再输入右侧编码（逗号分隔）；留空解除。</p>'+m.pairs.filter(g=>g.left.length).map(g=>'<button type="button" data-pair-context="'+id+'" data-pair-left="'+e(g.left[0].sku_id)+'">'+e(g.left[0].name||g.left[0].sku_id)+(g.ambiguous?'（候选竞争，待确认）':'')+'</button>').join(' ')+' <button type="button" data-pair-reset="'+id+'">恢复自动排版</button></details>';}
if(typeof document!=='undefined')document.addEventListener('click',ev=>{const b=ev.target.closest('[data-pair-context],[data-pair-reset]');if(!b)return;const rows=contexts.get(b.dataset.pairContext||b.dataset.pairReset);if(!rows)return;const key=layoutKey(rows);if(b.dataset.pairReset)overrides.delete(key);else{const right=rows.map(normalize).filter(r=>r.kind!=='unknown'&&(r.delta>0||r.kind==='extra'&&r.recv>0));const value=prompt('可选多收 / 单外编码：\n'+right.map(r=>r.sku_id+' · '+r.name).join('\n')+'\n输入要并排的编码，逗号分隔；留空解除：',(overrides.get(key)?.[b.dataset.pairLeft]||[]).join(','));if(value===null)return;const ids=[...new Set(value.split(/[,，\s]+/).filter(Boolean))];if(ids.some(id=>!right.some(r=>r.sku_id===id))){alert('含不在当前多收 / 单外列表的编码，未更改');return;}const manual={...(overrides.get(key)||{})};for(const k of Object.keys(manual))manual[k]=manual[k].filter(id=>!ids.includes(id));manual[b.dataset.pairLeft]=ids;overrides.set(key,manual);}const panel=b.closest('.pair-layout');if(panel)panel.outerHTML=table(rows,panel.dataset.only==='1');});
function quantity(r){return r.kind==='extra'?{label:'单外实收',value:n(r.recv)}:r.delta<0?{label:'少收',value:n(-r.delta)}:{label:'多收',value:n(r.delta)};}
function countText(r){return r.kind==='neutral'?'实收 '+n(r.recv):r.kind==='extra'?'采购单外':r.kind==='unknown'?r.reason||'待核实':'应收 '+n(r.plan)+' / 实收 '+n(r.recv)+(r.kind==='good'?' · 一致':'');}
function product(r){return '<strong>'+e(r.name||'未提供商品名')+'</strong><div class="pair-code">编码：'+e(r.sku_id)+'</div>'+(r.spec&&r.spec!==r.name?'<div>'+e(r.spec)+'</div>':'')+(r.sku_code?'<div class="pair-sub">条码：'+e(r.sku_code)+'</div>':'')+'<div class="pair-count">'+e(countText(r))+'</div>';}
function pairCells(r,count){const span=' rowspan="'+count+'"',kind=r?r.kind:'pair-empty';if(!r)return '<td'+span+' colspan="2" class="pair-cell pair-empty">—</td>';const q=quantity(r),size=Math.min(32,Math.max(12,Math.floor(112/Math.max(4,q.value.length))));return '<td'+span+' class="pair-cell '+kind+'">'+product(r)+'</td><td'+span+' class="pair-qty '+kind+'"><span class="pair-qty-label">'+e(q.label)+'</span><strong class="pair-qty-value" style="font-size:'+size+'px">'+e(q.value)+'</strong></td>';}
function table(input,only=false){const m=model(input);let html='<div class="pair-note">相似品仅并排参考，仍按各自 SKU 核对；不抵消差异、不自动替换商品。</div><table class="pair-table"><colgroup><col><col class="pair-qty-col"><col><col class="pair-qty-col"></colgroup><thead><tr><th>少收 · 采购应收</th><th class="pair-qty-head">少收数量</th><th>多收 / 采购单外 · 实收</th><th class="pair-qty-head">多收 / 单外数量</th></tr></thead><tbody>';for(const g of m.pairs){const count=Math.max(g.left.length,g.right.length,1);for(let i=0;i<count;i++){html+='<tr>';for(const key of ['left','right']){const a=g[key];if(a.length<=1){if(i===0)html+=pairCells(a[0],count);}else html+=pairCells(a[i],1);}html+='</tr>';}}html+='</tbody></table>';if(!m.pairs.length)html+='<p>无少收 / 多收差异</p>';if(m.unknown.length)html+='<h3>待核实（不判定匹配）</h3>'+m.unknown.map(r=>'<div class="pair-full unknown">'+product(r)+'</div>').join('');if(!only&&m.good.length)html+='<details open><summary>已匹配明细 · '+m.good.length+' 项</summary>'+m.good.map(r=>'<div class="pair-full good">'+product(r)+'</div>').join('')+'</details>';return '<div class="pair-layout" data-only="'+(only?1:0)+'">'+html+manualPanel(input)+'</div>';}
async function image(rows,options={}){await(document.fonts&&document.fonts.ready);const c=document.createElement('canvas'),ctx=c.getContext('2d'),ops=[],W=1200,font='"Microsoft YaHei",sans-serif';let y=30;function wrap(s,w,size=24){ctx.font=size+'px '+font;let list=[],line='';for(const ch of String(s??'')){if(ch==='\n'||line&&ctx.measureText(line+ch).width>w){list.push(line);line=ch==='\n'?'':ch}else line+=ch}list.push(line);return list}function rect(x,y,w,h,color){ops.push({x,y,w,h,color})}function put(s,x,top,w,size=24,bold=false){const lines=wrap(s,w,size);lines.forEach((t,i)=>ops.push({t,x,y:top+i*(size+12),size,bold,color:'#263548'}));return lines.length*(size+12)}function line(s,size=24){y+=put(s,32,y,1136,size,true)+14}function contents(r){return [r.name||'未提供商品名','编码：'+r.sku_id,...(r.spec&&r.spec!==r.name?[r.spec]:[]),...(r.sku_code?['条码：'+r.sku_code]:[]),countText(r)];}
function hasQuantity(r){return r&&(r.kind==='diff'||r.kind==='extra');}
const QW=124;
function cellHeight(r,w){return r?Math.max(120,contents(r).reduce((s,t)=>s+wrap(t,w-32-(hasQuantity(r)?QW:0),24).length*36,28)):70;}
function cell(r,x,top,w,h){rect(x,top,w,h,colors[r?.kind]||'#fafbfc');if(r){const split=hasQuantity(r);let py=top+14;contents(r).forEach((t,i)=>py+=put(t,x+16,py,w-32-(split?QW:0),24,i===0));if(split){const q=quantity(r),qx=x+w-QW;rect(qx,top,2,h,'#b9c6d2');let size=36;ctx.font='600 '+size+'px '+font;while(size>12&&ctx.measureText(q.value).width>QW-20){size--;ctx.font='600 '+size+'px '+font;}const labelHeight=wrap(q.label,QW-20,22).length*34;const qy=top+(h-labelHeight-size-12)/2;put(q.label,qx+10,qy,QW-20,22,true);ctx.font='600 '+size+'px '+font;put(q.value,qx+(QW-ctx.measureText(q.value).width)/2,qy+labelHeight+6,QW-20,size,true);}}else put('—',x+18,top+20,w-36);rect(x,top+h-2,w,2,'#b9c6d2');}
line(options.title||'收货差异对照',34);for(const h of options.headers||[])line(h,23);line('绿色：一致 / 黄色：数量差异 / 灰色：采购单外或待核实',23);line('相似品仅并排参考，不合并 SKU、不抵消差异。',22);const m=model(rows);rect(32,y,1136,60,'#dbe5f1');put('少收 · 采购应收',48,y+12,524,26,true);put('多收 / 采购单外 · 实收',622,y+12,524,26,true);y+=60;
for(const g of m.pairs){let count=Math.max(g.left.length,g.right.length,1),heights=Array(count).fill(80);for(const key of ['left','right'])if(g[key].length>1)g[key].forEach((r,i)=>heights[i]=Math.max(heights[i],cellHeight(r,560)));let total=heights.reduce((a,b)=>a+b,0);for(const key of ['left','right'])if(g[key].length===1)total=Math.max(total,cellHeight(g[key][0],560));heights[count-1]+=total-heights.reduce((a,b)=>a+b,0);for(const [key,x] of [['left',32],['right',608]]){const a=g[key];if(a.length<=1)cell(a[0],x,y,560,total);else{let top=y;for(let i=0;i<count;i++){cell(a[i],x,top,560,heights[i]);top+=heights[i]}}}y+=total+8;}
if(!m.pairs.length)line('无少收 / 多收差异');function full(list,title){if(!list.length)return;line(title,26);for(const r of list){const h=cellHeight(r,1136);cell(r,32,y,1136,h);y+=h+6}}
full(m.unknown,'待核实（不判定匹配）');if(!options.only)full(m.good,'已匹配明细');for(const section of options.sections||[])full(section.rows.map(r=>({...r,kind:'neutral'})),section.title);line('仅核对 SKU 和数量；未自动确认入库。',22);y+=24;if(y>30000)throw Error('图片超过单张高度上限，未截断；请仅复制差异、关闭各物流单明细或导出 CSV');c.width=W;c.height=Math.ceil(y);ctx.fillStyle='#fff';ctx.fillRect(0,0,W,c.height);ctx.textBaseline='top';for(const o of ops){ctx.fillStyle=o.color;if(o.t!==undefined){ctx.font=(o.bold?'600 ':'')+o.size+'px '+font;ctx.fillText(o.t,o.x,o.y)}else ctx.fillRect(o.x,o.y,o.w,o.h)}const blob=await new Promise(resolve=>c.toBlob(resolve,'image/png'));if(!blob)throw Error('图片生成失败');return {blob,dataUrl:c.toDataURL('image/png'),width:W,height:c.height};}
window.PurchasePairs={model,table,image,similarity,colors,normalize};})();
