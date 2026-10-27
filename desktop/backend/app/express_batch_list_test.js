// 快递单登记「本批次物流单号」：几百个单号时最新在上、标题显示数量、按 id 复用节点（扫描时不整表重画）、搜索筛选 / 定位。
// 用极简假 DOM 直接加载 web/receiving-desk.js（不依赖 jsdom）。
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert');
let created=0;
class El{constructor(tag,id){this.tagName=tag;this.id=id||'';this.children=[];this.parentNode=null;this.dataset={};this.attrs={};this._cls=new Set();this.hidden=false;this.value='';this._text='';this._html='';this.scrollTop=0;this.clientHeight=260;this.listeners={};
  const self=this;this.classList={toggle(c,on){if(on===undefined)on=!self._cls.has(c);on?self._cls.add(c):self._cls.delete(c);},contains:c=>self._cls.has(c),add:c=>self._cls.add(c)};}
 set className(v){this._cls=new Set(String(v).split(/\s+/).filter(Boolean));} get className(){return [...this._cls].join(' ');}
 set innerHTML(v){this.children.forEach(c=>c.parentNode=null);this.children=[];this._html=String(v);if(this._html.includes('desk-empty')){const d=new El('div');d.className='desk-empty';this.appendChild(d);}}
 get innerHTML(){return this._html;}
 set textContent(v){this.children.forEach(c=>c.parentNode=null);this.children=[];this._text=String(v);} get textContent(){return this._text;}
 appendChild(c){return this.insertBefore(c,null);}
 insertBefore(c,ref){if(c.parentNode)c.remove();const i=ref?this.children.indexOf(ref):-1;if(i<0)this.children.push(c);else this.children.splice(i,0,c);c.parentNode=this;return c;}
 remove(){if(this.parentNode){const p=this.parentNode;p.children.splice(p.children.indexOf(this),1);this.parentNode=null;}}
 get firstChild(){return this.children[0]||null;}
 get nextSibling(){if(!this.parentNode)return null;const s=this.parentNode.children;return s[s.indexOf(this)+1]||null;}
 get offsetTop(){return this.parentNode?this.parentNode.children.filter(c=>!c.hidden).indexOf(this)*48:0;} get offsetHeight(){return 44;}
 querySelector(sel){return sel==='.desk-empty'?this.children.find(c=>c.classList.contains('desk-empty'))||null:null;}
 setAttribute(k,v){this.attrs[k]=String(v);} getAttribute(k){return this.attrs[k];}
 addEventListener(t,f){(this.listeners[t]=this.listeners[t]||[]).push(f);} dispatchEvent(){} focus(){} select(){}
 click(){(this.parentNode&&this.parentNode.onclick)&&this.parentNode.onclick({target:{closest:()=>this}});}
 closest(){return null;}}
const els={};const get=id=>els[id]||(els[id]=new El('div',id));
const document={body:{dataset:{desk:'express'}},getElementById:get,createElement:t=>{created++;return new El(t);},addEventListener(){},querySelector:()=>null};
const window={};window.parent=window;
const ctx={window,document,location:{search:''},history:{replaceState(){}},sessionStorage:{setItem(){},removeItem(){}},Event:class{constructor(t){this.type=t;}},URLSearchParams,console};
vm.createContext(ctx);vm.runInContext(fs.readFileSync(path.join(__dirname,'web','receiving-desk.js'),'utf8'),ctx);
const D=window.ReceivingDesk;assert.ok(D);
const N=500,batches=[];for(let i=1;i<=N;i++)batches.push({id:i,tracking_no:'SF'+String(1000000000000+i),total:i%7+1,lines:[]});
const S=list=>({session:{id:9,state:'active'},locked:false,sku_count:3,total:100,batches:list});
let t0=Date.now();D.render(S(batches),N);const first=Date.now()-t0;
const box=get('desk-batches');
assert.strictEqual(box.children.length,N);
assert.strictEqual(box.children[0].dataset.selectBatch,N,'最新扫描的在最上面');
assert.strictEqual(box.children[N-1].dataset.selectBatch,1);
assert.strictEqual(get('desk-batches-title').textContent,'本批次物流单号（500）');
assert.ok(box.children[0].classList.contains('active'));
// 再扫一个新单号：只新建 1 个节点，其余节点原样复用、不重画
const before=box.children.slice(),c0=created,html0=before.map(n=>n._html);
batches.push({id:N+1,tracking_no:'YT9999',total:1,lines:[]});
t0=Date.now();D.render(S(batches),N+1);const second=Date.now()-t0;
assert.strictEqual(created-c0,1,'每次扫描只新建一个按钮');
assert.strictEqual(box.children[0].dataset.selectBatch,N+1);
assert.strictEqual(box.children.length,N+1);
assert.ok(before.every(n=>box.children.includes(n)),'旧节点复用');
const rewritten=before.filter((n,i)=>n._html!==html0[i]).length;
assert.ok(rewritten<=1,'只有原来高亮的那一行更新（取消高亮），实际 '+rewritten);
assert.strictEqual(get('desk-batches-title').textContent,'本批次物流单号（501）');
// 搜索：输入部分单号筛选，回车选中第一条
const input=get('desk-batch-search');input.value='1000000000123';input.listeners.input[0]();
assert.strictEqual(box.children.filter(n=>!n.hidden).length,1);
assert.strictEqual(get('desk-package-count').textContent,'匹配 1 单');
get('pending').hidden=true;let picked=null;get('batches').dispatchEvent=()=>{picked=get('batches').value;};
input.listeners.keydown[0]({key:'Enter',preventDefault(){}});
assert.strictEqual(String(picked),'123');
input.value='';input.listeners.input[0]();
assert.strictEqual(box.children.filter(n=>!n.hidden).length,N+1);
// 删除一个物流单：对应节点移除
D.render(S(batches.filter(b=>b.id!==200)),N+1);assert.strictEqual(box.children.length,N);
console.log('express-batch-list ok ('+N+' 单首次 '+first+' ms，追加一单 '+second+' ms)');
