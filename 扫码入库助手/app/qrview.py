"""Render only the live official provider frame; never generate OAuth URLs or save QR tokens."""
import json
import urllib.parse
import cdp

SCRIPT = r'''(function(){
var label=KIND==='dingtalk'?'钉钉扫码登录':'微信扫码登录';
var expected=KIND==='dingtalk'?'login.dingtalk.com':'open.weixin.qq.com';
var frames=Array.from(document.querySelectorAll('iframe'));
var frame=frames.find(function(f){try{return new URL(f.src).hostname===expected;}catch(e){return false;}});
var tabs=Array.from(document.querySelectorAll('div[class*="qr-tabs-tab"]'));
var tab=tabs.find(function(t){return t.textContent.trim()===label;});
if(tab && !tab.className.includes('active')){tab.click();return {waiting:true};}
if(frame){var r=frame.getBoundingClientRect();if(r.width>100&&r.height>100&&r.width<700&&r.height<800&&getComputedStyle(frame).visibility!=='hidden')return {clip:{x:r.left+scrollX,y:r.top+scrollY,width:r.width,height:r.height,scale:1}};}
if(tab)return {waiting:true};
// Toggle only the known official login control; never click consent or verification controls.
var toggle=document.querySelector('img.toogle-img');
if(toggle && (!window.__jstQrToggleAt || Date.now()-window.__jstQrToggleAt>1500)){window.__jstQrToggleAt=Date.now();toggle.click();return {waiting:true};}
return {waiting:true};
})()'''

def snapshot(port, kind):
    if kind not in ('weixin','dingtalk'):
        return {}
    targets=cdp._http_json(port, '/json/list', timeout=2)
    pages=[t for t in targets if t.get('type') == 'page']
    login=[]
    for t in pages:
        url=urllib.parse.urlparse(t.get('url',''))
        if url.scheme == 'https' and url.hostname == 'www.erp321.com' and url.path.lower() == '/login.aspx':
            login.append(t)
    if len(login) != 1:
        return {'qr_kind':kind, 'pending':'扫码正在跳转；如需绑定或安全验证，请使用独立的“官方网页登录”入口完成。'}
    target=login[0]['id']
    expression=SCRIPT.replace('KIND',json.dumps(kind))
    r=cdp.command(port,'Runtime.evaluate',{'expression':expression,'returnByValue':True},timeout=3,target_id=target)
    value=r.get('result',{}).get('value') or {}
    clip=value.get('clip')
    if not clip:
        return {'qr_kind':kind,'pending':'正在加载官方二维码；如长时间未显示，请刷新二维码或使用官方网页登录。'}
    shot=cdp.command(port,'Page.captureScreenshot',{'format':'png','clip':clip,'captureBeyondViewport':True},timeout=4,target_id=target)
    image=shot.get('data','')
    if not image.startswith('iVBOR') or len(image)>2000000:
        return {'qr_kind':kind,'pending':'二维码图像暂不可用，请刷新或使用官方网页登录。'}
    return {'qr_kind':kind,'qr_image':'data:image/png;base64,'+image}


def refresh(port):
    """Reload only the owned official login target; never construct/replay OAuth URLs."""
    targets = cdp._http_json(port, '/json/list', timeout=2)
    pages = []
    for target in targets:
        url = urllib.parse.urlparse(target.get('url', ''))
        if (target.get('type') == 'page' and url.scheme == 'https'
                and url.hostname == 'www.erp321.com' and url.path.lower() == '/login.aspx'):
            pages.append(target)
    if len(pages) != 1:
        return False
    cdp.command(port, 'Page.reload', {'ignoreCache': True}, timeout=3, target_id=pages[0]['id'])
    return True
