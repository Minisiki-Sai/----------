/* ============================================================================
 * DNACT 语音包生成助手 v1.1
 * 适用：火山引擎控制台 → 语音合成体验页（tiptap/ProseMirror 编辑器 + mp3 输出）
 *
 * v1.1 改动：
 *   - 输入框改为富文本策略（execCommand / paste / 直接赋值 三路兜底）
 *   - 找不到合成按钮不再直接退出，改为「等自动合成 → 试按钮 → 试快捷键」
 *   - 采到的 mp3 自动解码并重封装为 24kHz 单声道 16bit wav（不依赖 ffmpeg）
 *   - 诊断新增「点击轨迹 / 按键记录 / 写入回读」，用来定位真正触发合成的元素
 *
 * 用法：
 *   F12 → Console → 输入 allow pasting 回车 → 粘贴本文件 → 回车
 *   DNACT.start({limit:3})   先跑 3 句验证
 *   DNACT.start()            全量 158 句
 * ========================================================================== */
(function () {
  'use strict';

  var VERSION = '1.1.0';
  var PHRASES = [
    ["语音播报试听，请注意战斗机制", "6e374ea8798f2e55"],
    ["10秒内无旋风，可输出", "0e246e82a97be059"],
    ["15秒内无旋风，可输出", "02b8633c1fe65961"],
    ["20秒内无水球机制，可输出", "80ec65981a45af43"],
    ["20秒内无追踪，可输出", "6b4f8bb657a09dbd"],
    ["35秒输出时间，注意其他机制", "9fcb051c22ea1114"],
    ["三连砸已好", "5c6f723865ce75ef"],
    ["上升重力已好", "4290e6b75c3d325e"],
    ["先逆再顺，注意躲避", "61f3b4485caa92aa"],
    ["先顺再逆，注意躲避", "b6cda978376bf1c4"],
    ["六芒星已好", "b5289230837b73dc"],
    ["冰捆已好", "47e8efaf15b287d7"],
    ["准备冲撞机制", "5d93d16238c085bd"],
    ["准备分边", "0f4382d5f0b198d2"],
    ["准备水球站位", "6a3846ecde778c4a"],
    ["准备龙息", "1ce6e18b8b69c4e1"],
    ["即将冰眼", "1258da2d7eed9b6a"],
    ["即将龙息", "9e13ac2785b01120"],
    ["召唤触手已好", "a0bfd3d7034e68e3"],
    ["叹息之墙已好，前往点位", "7d9315f5a0490f99"],
    ["吼叫已好", "66a93fd51a9bf27f"],
    ["圣骑士出现", "53c6eead6147450a"],
    ["地面激光已好", "4d3cbbc86fd18581"],
    ["小心火环、中心旋风、撞球、点名", "3fd7a95ddd69a38f"],
    ["小心火环、冰墙", "6f746f934c4ac5c0"],
    ["小心火环、闪光、撞球、点名", "df7a77a218430d93"],
    ["小心践踏", "a5b4102839ac3e71"],
    ["小心追踪火、中心旋风、撞球、点名", "89e781566657378a"],
    ["小心追踪火、闪光、撞球、点名", "cb266c2f56a320e2"],
    ["小追已好", "375337af7829230b"],
    ["所有爆炸兵消失", "d8dfe2583ae46c53"],
    ["旋转已好", "8714c89df49fe13c"],
    ["旋转激光已好", "48ecdaa0c3abb5fd"],
    ["旋风已好", "860a8111a136a6dc"],
    ["沙虫已好", "a759a82667c1dfee"],
    ["注意上升重力", "c7716f50844aec46"],
    ["注意元素点名", "515eb6659b8678d9"],
    ["注意光明之怒点名", "618655a207fe72bb"],
    ["注意冰捆", "11a7964d4e53b2c3"],
    ["注意冰灵点名", "fedcc97275eb24a2"],
    ["注意冰眼", "5a6be54fe9e1d2c5"],
    ["注意冲锋1次", "08990a19154aa020"],
    ["注意冲锋2次", "9302ea4bd2775041"],
    ["注意冲锋一次接砸地", "d62c29bc0192140a"],
    ["注意冲锋两次接砸地", "fac8a827d2d1391c"],
    ["注意刀锋舞者点名", "c09fb0a947d7e1e2"],
    ["注意刺客点名", "dd8f70aadaf5af60"],
    ["注意剑圣点名", "11519044ace4fd19"],
    ["注意剑皇点名", "a00648fad9c1fc98"],
    ["注意十字军点名", "42ac602a25f5a01b"],
    ["注意吐息", "5603555fc5e932cd"],
    ["注意吼叫", "18d56283075d90f4"],
    ["注意呐喊者点名", "c5d9fe404e85a6d9"],
    ["注意噬魂者点名", "79ed30461ffe64a3"],
    ["注意圣徒点名", "07bcb353e7825062"],
    ["注意圣骑士点名", "03f5c072a75b8014"],
    ["注意地面激光", "a24862e4b909abc0"],
    ["注意复仇者点名", "78480a33f640f62a"],
    ["注意大追", "86f975f917c777e2"],
    ["注意天空", "2bad07111f205e00"],
    ["注意学者点名", "a2b26f90fea2e0f3"],
    ["注意工程点名", "2220f0def9bd3c35"],
    ["注意弓箭点名", "89e7572e371cc36a"],
    ["注意影点名", "dbd97d5aa91b143f"],
    ["注意影舞者点名", "e2da88e924c03dd1"],
    ["注意战士点名", "862e79d79e26dc1b"],
    ["注意战神点名", "f3f39963e354022d"],
    ["注意拉冰，三连啄后前往右侧安全区", "9f0eb608b588b0f6"],
    ["注意拉冰，三连啄后前往左侧安全区", "b94215ab1ac21ace"],
    ["注意拉冰，前往龙右侧安全区", "fe77680735392ac6"],
    ["注意拉冰，前往龙左侧安全区", "a4855e246687a36b"],
    ["注意旋转", "a3e3012a403730bd"],
    ["注意旋转激光逆时针", "9d109fd44bc99177"],
    ["注意旋转激光顺时针", "159265189cd93cec"],
    ["注意旋风", "3b14d9507eeba911"],
    ["注意时空领主点名", "8c82c9a518b1aa06"],
    ["注意暗之使徒点名", "db3eadf58751bb3e"],
    ["注意暗点名", "f79c16defc13f8fb"],
    ["注意曜点名", "6e658692ac08c85e"],
    ["注意月之领主点名", "d91e0bd7a587a31f"],
    ["注意机械大师点名", "b5c81244313b481b"],
    ["注意毁灭者点名", "88a0c98a80c997ea"],
    ["注意沙虫", "d91eb22ddc28e780"],
    ["注意法师点名", "67f00d9aea57e187"],
    ["注意游侠点名", "f92dbaad97f1efe6"],
    ["注意火舞点名", "293af0e135e41280"],
    ["注意灵魂舞者点名", "f3bbc4b74bf49620"],
    ["注意炼金圣士点名", "79135e68b40892b1"],
    ["注意炼金点名", "79381822ed2f60a5"],
    ["注意烈点名", "3bfbeaf87eaef8d8"],
    ["注意牧师点名", "787c690545ecf66e"],
    ["注意狂战士点名", "938da1d1c4322d0c"],
    ["注意狙翎点名", "2a7bebd656d07e46"],
    ["注意猎人点名", "5f4502d85bb7ce7b"],
    ["注意甩尾", "eea0724b1e24e373"],
    ["注意直线", "f3679b3f5968cd07"],
    ["注意砸地", "a5882bd3be6d7bd8"],
    ["注意砸地沙虫", "fa5631f499b2072c"],
    ["注意砸地，前往3点", "53a45bbd72f3200a"],
    ["注意砸地，前往9点", "526687e66413e854"],
    ["注意祭司点名", "1714c37b29d5e11e"],
    ["注意箭神点名", "61830456d8d5777e"],
    ["注意舞娘点名", "3eef2a0769233edc"],
    ["注意舞者点名", "5ea7283e866c6ce1"],
    ["注意药剂师点名", "ea46c7b3a61f4fe4"],
    ["注意触手", "76ea65f299598114"],
    ["注意贤者点名", "8c984dbb9e035f68"],
    ["注意踢飞", "82064deffb2f4ebc"],
    ["注意连续追踪", "6e541559a38fe049"],
    ["注意追踪", "65654eb2749a8f13"],
    ["注意重炮手点名", "9f3fee5b78dfd975"],
    ["注意银色猎人点名", "a9c299600befd11a"],
    ["注意闪光转身", "3855493b0d23210b"],
    ["注意雷神点名", "c51f0be4130ba846"],
    ["注意风行者点名", "f7361b9daa25bc47"],
    ["注意飞天", "b52c7d58079825e7"],
    ["注意魔导点名", "76364339e9d33543"],
    ["注意魔羽点名", "051650c0fe30fd87"],
    ["注意黑暗复仇者点名", "5789ab5322eb6eda"],
    ["注意黑暗女王点名", "870cc1e17fb3b0a2"],
    ["注意黑暗萨满点名", "51d1ae9742314a82"],
    ["注意黑洞", "46f72884172c8a9f"],
    ["注意龙息", "528a2cd4164885ab"],
    ["火陨石已好", "b00658f3e6b8c800"],
    ["炸弹机制已好", "533a5351ef9f314d"],
    ["点名已好", "528d2d0a6cc258ef"],
    ["爆炸兵出现", "c80b80a07245c382"],
    ["牛头怪即将出现", "4b1683d13fb0b049"],
    ["狮子，三连冲已好", "e0b0a981cc8d85ef"],
    ["狮子，注意三连冲", "ebe634ea0e164536"],
    ["甩尾已好", "bc8b7d82a7d5e937"],
    ["石头人，三连砸已好", "9dd8933a010726f6"],
    ["石头人，注意风暴", "e670ede1396ef63b"],
    ["砸地已好", "bb36b8c4bbd8e017"],
    ["红衣追随者出现", "6ccbd199ccd36faf"],
    ["红衣追随者爆炸，注意躲避", "1f0967d69c805515"],
    ["红衣追随者退场", "ae4091b76c4a45d6"],
    ["蝎子，旋转已好", "ec4487d8699157e4"],
    ["蝎子，注意旋转", "a8900eca17b00087"],
    ["跳", "f0dc90703bef68e0"],
    ["践踏已好", "efa416e3abc306ec"],
    ["连续追踪已好", "49904362de9b72e3"],
    ["追踪已好", "88ef349a1b641f9d"],
    ["逆时针，注意躲避", "e911f132daf8a557"],
    ["速击杀牛头怪", "70cb92ca58924a4c"],
    ["闪光已好", "0247bf0423608e6c"],
    ["顺时针，注意躲避", "f58a215ee859b79a"],
    ["风壁，前往中央", "240ed63fb31cc615"],
    ["飞天技能已好", "c045a49c4b9053d1"],
    ["飞天追踪已取消", "181f19d050eb1dd1"],
    ["飞天追踪已好", "31eb7ed16a4187a9"],
    ["马上拉冰", "2895c36cee0c38a0"],
    ["马上起飞", "ca216506f6c0c8fd"],
    ["马上飞天、注意上炮", "12568564641cb24c"],
    ["马上飞天、注意躲避龙息消灭暗影", "51f9012e083efde9"],
    ["黑影即将爆炸", "a5525839fe6c4cff"],
    ["黑洞已好", "84b6c94aa51f9db6"],
    ["龙息已好", "aebcde24cbcc4adc"]
  ];

  var CAP = {
    audio: [], blobMap: new Map(), textLog: [],
    wsCount: 0, elementCount: 0, hooked: false
  };
  var TRACE = { clicks: [], keys: [] };
  var ST = { running: false, stop: false, cur: -1, ok: 0, fail: [], sel: {}, auto: 0, click: 0, key: 0 };
  var ACTX = null;

  function log() {
    var a = Array.prototype.slice.call(arguments);
    console.log.apply(console, ['%c[DNACT]', 'color:#3aa0ff;font-weight:bold'].concat(a));
  }
  function warn() {
    var a = Array.prototype.slice.call(arguments);
    console.warn.apply(console, ['%c[DNACT]', 'color:#e6a700;font-weight:bold'].concat(a));
  }
  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  // ------------------------------------------------------------------ DOM 探测
  function visible(el) {
    if (!el || !el.isConnected) return false;
    var r = el.getBoundingClientRect();
    if (r.width < 20 || r.height < 8) return false;
    var s = getComputedStyle(el);
    if (s.visibility === 'hidden' || s.display === 'none') return false;
    if (parseFloat(s.opacity) < 0.15) return false;
    return true;
  }

  function findInput() {
    var sel = '[contenteditable="true"],[contenteditable=""],textarea,input[type="text"]';
    var list = Array.prototype.slice.call(document.querySelectorAll(sel)).filter(visible);
    if (!list.length) return null;
    var best = null, bestScore = -1;
    list.forEach(function (el) {
      var r = el.getBoundingClientRect();
      var score = r.width * r.height;
      var hint = (el.getAttribute('placeholder') || '') + (el.getAttribute('aria-label') || '') +
        (el.className || '');
      if (/搜索|search|查找|filter|input-search/i.test(hint)) score *= 0.01;
      if (/text|content|editor|tiptap|prosemirror|语音|输入/i.test(hint)) score *= 3;
      if (el.tagName === 'TEXTAREA') score *= 1.5;
      if (score > bestScore) { bestScore = score; best = el; }
    });
    return best;
  }

  // 合成按钮候选：排除示例/重置/清空/下载等干扰项
  function findCandidates() {
    var BAD = /示例|重置|清空|删除|下载|上传|复制|历史|保存|收藏|分享|更多|设置|帮助|文档|购买|登录/;
    var GOOD = ['合成', '生成', '开始', '转换', '朗读', '播放', '试听'];
    var nodes = Array.prototype.slice.call(
      document.querySelectorAll('button,[role="button"],a,[class*="btn"],[class*="button"]')
    ).filter(visible);
    var out = [];
    nodes.forEach(function (el) {
      var t = (el.innerText || el.textContent || '').trim().replace(/\s+/g, '');
      var aria = (el.getAttribute('aria-label') || '') + (el.getAttribute('title') || '');
      var probeText = t || aria;
      if (!probeText || probeText.length > 12) return;
      if (BAD.test(probeText)) return;
      var s = 0;
      GOOD.forEach(function (w) {
        if (t === w) s = Math.max(s, 100);
        else if (t.indexOf(w) >= 0) s = Math.max(s, 62);
      });
      if (aria && GOOD.some(function (w) { return aria.indexOf(w) >= 0; })) s = Math.max(s, 55);
      if (!s) return;
      var r = el.getBoundingClientRect();
      if (r.width < 24) return;
      s += Math.min(r.width / 60, 4);
      out.push({ el: el, score: s, text: t || ('[' + aria + ']'), cls: String(el.className || '').slice(0, 50) });
    });
    out.sort(function (a, b) { return b.score - a.score; });
    return out;
  }

  function findSubmit() {
    var c = findCandidates();
    return c.length ? c[0].el : null;
  }

  function desc(el) {
    if (!el) return null;
    var r = el.getBoundingClientRect();
    return {
      tag: el.tagName,
      cls: String(el.className || '').slice(0, 60),
      placeholder: el.getAttribute('placeholder') || '',
      text: (el.innerText || el.textContent || '').trim().slice(0, 24),
      size: Math.round(r.width) + 'x' + Math.round(r.height),
      disabled: !!el.disabled,
      editable: !!el.isContentEditable
    };
  }

  // ------------------------------------------------------------------ 写入文本
  function readText(el) {
    return el.isContentEditable ? (el.innerText || el.textContent || '') : (el.value || '');
  }

  function setText(el, text) {
    el.focus();
    var got = function () { return readText(el).indexOf(text) >= 0; };

    // S1 execCommand insertText（tiptap / ProseMirror 首选）
    try {
      var s = window.getSelection();
      var rg = document.createRange();
      rg.selectNodeContents(el);
      s.removeAllRanges(); s.addRange(rg);
      document.execCommand('insertText', false, text);
    } catch (e) { }
    if (got()) return 'execCommand';

    // S2 伪造 paste
    try {
      var dt = new DataTransfer();
      dt.setData('text/plain', text);
      el.dispatchEvent(new ClipboardEvent('paste', {
        bubbles: true, cancelable: true, clipboardData: dt
      }));
    } catch (e) { }
    if (got()) return 'paste';

    // S3 直接赋值
    try {
      if (el.isContentEditable) {
        el.textContent = text;
        el.dispatchEvent(new InputEvent('input', { bubbles: true, data: text, inputType: 'insertText' }));
      } else {
        var proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
        var d = Object.getOwnPropertyDescriptor(proto, 'value');
        if (d && d.set) d.set.call(el, text); else el.value = text;
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
      }
    } catch (e) { }
    return got() ? 'assign' : 'failed';
  }

  function clickEl(el) {
    try {
      ['pointerdown', 'mousedown', 'pointerup', 'mouseup'].forEach(function (t) {
        el.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window }));
      });
      el.click();
    } catch (e) { }
  }

  function fireKey(el, combo) {
    var init = {
      key: combo.key, code: combo.key, bubbles: true, cancelable: true,
      ctrlKey: !!combo.ctrl, metaKey: !!combo.meta, shiftKey: !!combo.shift, altKey: !!combo.alt
    };
    ['keydown', 'keypress', 'keyup'].forEach(function (t) {
      el.dispatchEvent(new KeyboardEvent(t, init));
    });
  }

  // ---------------------------------------------------------------- 音频采集钩子
  function looksLikeAudio(u8) {
    if (!u8 || u8.length < 12) return null;
    if (u8[0] === 0x52 && u8[1] === 0x49 && u8[2] === 0x46 && u8[3] === 0x46) return 'wav';
    if (u8[0] === 0x49 && u8[1] === 0x44 && u8[2] === 0x33) return 'mp3';
    if (u8[0] === 0xFF && (u8[1] & 0xE0) === 0xE0) return 'mp3';
    if (u8[0] === 0x4F && u8[1] === 0x67 && u8[2] === 0x67 && u8[3] === 0x53) return 'ogg';
    if (u8[0] === 0x66 && u8[1] === 0x4C && u8[2] === 0x61 && u8[3] === 0x43) return 'flac';
    return null;
  }

  function b64ToU8(b64) {
    try {
      var bin = atob(b64);
      var u8 = new Uint8Array(bin.length);
      for (var i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
      return u8;
    } catch (e) { return null; }
  }

  function harvestBytes(u8, src) {
    var kind = looksLikeAudio(u8);
    if (!kind) return false;
    if (u8.length < 2000) return false;
    CAP.audio.push({ kind: kind, bytes: u8, src: src, sz: u8.length });
    return true;
  }

  function harvestText(text, src) {
    if (!text || text.length < 500) return 0;
    if (CAP.textLog.length < 20) CAP.textLog.push(text.slice(0, 200));
    var n = 0, m;
    var re = /"([A-Za-z0-9+\/]{1500,}={0,2})"/g;
    while ((m = re.exec(text))) {
      var u8 = b64ToU8(m[1]);
      if (u8 && harvestBytes(u8, src)) n++;
    }
    return n;
  }

  async function harvestFromElements() {
    var auds = Array.prototype.slice.call(document.querySelectorAll('audio'));
    var got = 0;
    for (var i = 0; i < auds.length; i++) {
      var el = auds[i];
      var src = el.currentSrc || el.src;
      if (!src || src.indexOf('blob:') !== 0) continue;
      if (el.__dnact_src === src) continue;
      el.__dnact_src = src;
      var blob = CAP.blobMap.get(src);
      if (!blob) {
        try { blob = await (await fetch(src)).blob(); } catch (e) { continue; }
      }
      if (blob && blob.size > 2000) {
        var b = new Uint8Array(await blob.arrayBuffer());
        if (harvestBytes(b, 'element')) got++;
      }
    }
    return got;
  }

  function installHooks() {
    if (CAP.hooked) return;
    CAP.hooked = true;

    var origCreate = URL.createObjectURL;
    URL.createObjectURL = function (obj) {
      var url = origCreate.call(URL, obj);
      try { if (obj instanceof Blob) CAP.blobMap.set(url, obj); } catch (e) { }
      return url;
    };

    var origFetch = window.fetch;
    window.fetch = function () {
      var args = arguments;
      return origFetch.apply(this, args).then(function (res) {
        try {
          var ct = (res.headers.get('content-type') || '').toLowerCase();
          if (ct.indexOf('audio') >= 0 || ct.indexOf('octet-stream') >= 0) {
            res.clone().arrayBuffer().then(function (b) { harvestBytes(new Uint8Array(b), 'fetch'); });
          } else if (ct.indexOf('json') >= 0 || ct.indexOf('text') >= 0) {
            res.clone().text().then(function (t) { harvestText(t, 'json'); });
          }
        } catch (e) { }
        return res;
      });
    };

    var origOpen = XMLHttpRequest.prototype.open;
    var origSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function () { return origOpen.apply(this, arguments); };
    XMLHttpRequest.prototype.send = function () {
      var xhr = this;
      xhr.addEventListener('load', function () {
        try {
          var rt = xhr.responseType;
          if (rt === 'arraybuffer' && xhr.response) harvestBytes(new Uint8Array(xhr.response), 'xhr');
          else if (rt === 'blob' && xhr.response) {
            xhr.response.arrayBuffer().then(function (b) { harvestBytes(new Uint8Array(b), 'xhr'); });
          } else if (typeof xhr.responseText === 'string') harvestText(xhr.responseText, 'xhr');
        } catch (e) { }
      });
      return origSend.apply(this, arguments);
    };

    var OrigWS = window.WebSocket;
    if (OrigWS) {
      var Patched = function (url, protocols) {
        var ws = protocols ? new OrigWS(url, protocols) : new OrigWS(url);
        ws.addEventListener('message', function (ev) {
          CAP.wsCount++;
          if (typeof ev.data === 'string') harvestText(ev.data, 'ws-text');
          else if (ev.data instanceof ArrayBuffer) harvestBytes(new Uint8Array(ev.data), 'ws-bin');
        });
        return ws;
      };
      Patched.prototype = OrigWS.prototype;
      ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'].forEach(function (k) { Patched[k] = OrigWS[k]; });
      window.WebSocket = Patched;
    }

    try {
      var AC = window.AudioContext || window.webkitAudioContext;
      if (AC && AC.prototype && AC.prototype.decodeAudioData) {
        var origDecode = AC.prototype.decodeAudioData;
        AC.prototype.decodeAudioData = function (buf) {
          try {
            if (buf && buf.byteLength > 2000) {
              var cp = buf.slice ? buf.slice(0) : buf;
              Promise.resolve(cp.arrayBuffer ? cp.arrayBuffer() : cp).then(function (b) {
                harvestBytes(new Uint8Array(b), 'webaudio');
              });
            }
          } catch (e) { }
          return origDecode.apply(this, arguments);
        };
      }
    } catch (e) { }

    try {
      new MutationObserver(function (muts) {
        muts.forEach(function (m) {
          Array.prototype.forEach.call(m.addedNodes || [], function (n) {
            if (!n.querySelectorAll) return;
            CAP.elementCount += (n.tagName === 'AUDIO' ? 1 : 0) + n.querySelectorAll('audio').length;
          });
        });
      }).observe(document.documentElement, { childList: true, subtree: true });
    } catch (e) { }

    // 点击 / 按键轨迹（定位真正触发合成的元素）
    document.addEventListener('click', function (e) {
      if (TRACE.clicks.length > 60) return;
      var t = e.target;
      if (!t || !t.tagName) return;
      var chain = [], n = t, i = 0;
      while (n && i < 3) {
        chain.push(n.tagName.toLowerCase() +
          (n.className ? '.' + String(n.className).split(/\s+/).slice(0, 2).join('.') : ''));
        n = n.parentElement; i++;
      }
      TRACE.clicks.push({
        chain: chain.join(' < '),
        text: (t.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 14),
        aria: (t.getAttribute && (t.getAttribute('aria-label') || t.getAttribute('title') || '')) || ''
      });
    }, true);

    document.addEventListener('keydown', function (e) {
      if (TRACE.keys.length > 40) return;
      if (e.key !== 'Enter') return;
      TRACE.keys.push({ key: e.key, ctrl: e.ctrlKey, meta: e.metaKey, shift: e.shiftKey, alt: e.altKey });
    }, true);
  }

  // ------------------------------------------------------------ mp3 → 24kHz wav
  function getCtx() {
    if (!ACTX) {
      var AC = window.AudioContext || window.webkitAudioContext;
      ACTX = new AC();
    }
    return ACTX;
  }

  function wavFromFloat(f32, sr) {
    var n = f32.length;
    var buf = new ArrayBuffer(44 + n * 2);
    var dv = new DataView(buf);
    var wr = function (o, s) { for (var i = 0; i < s.length; i++) dv.setUint8(o + i, s.charCodeAt(i)); };
    wr(0, 'RIFF'); dv.setUint32(4, 36 + n * 2, true); wr(8, 'WAVE');
    wr(12, 'fmt '); dv.setUint32(16, 16, true); dv.setUint16(20, 1, true); dv.setUint16(22, 1, true);
    dv.setUint32(24, sr, true); dv.setUint32(28, sr * 2, true);
    dv.setUint16(32, 2, true); dv.setUint16(34, 16, true);
    wr(36, 'data'); dv.setUint32(40, n * 2, true);
    var out = new Int16Array(buf, 44, n);
    for (var i = 0; i < n; i++) {
      var s = f32[i] < -1 ? -1 : (f32[i] > 1 ? 1 : f32[i]);
      out[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
    }
    return new Uint8Array(buf);
  }

  async function transcodeToWav(bytes, targetSr) {
    targetSr = targetSr || 24000;
    var ctx = getCtx();
    var ab = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
    var buf = await ctx.decodeAudioData(ab);
    var frames = Math.max(1, Math.ceil(buf.duration * targetSr));
    var off = new OfflineAudioContext(1, frames, targetSr);
    var src = off.createBufferSource();
    src.buffer = buf;
    src.connect(off.destination);
    src.start(0);
    var rendered = await off.startRendering();
    return wavFromFloat(rendered.getChannelData(0), targetSr);
  }

  async function ensureWav(item) {
    if (!item || item.kind === 'wav') return item;
    try {
      var u8 = await transcodeToWav(item.bytes);
      item.bytes = u8;
      item.kind = 'wav';
      item.sz = u8.length;
      item.src += '>wav';
    } catch (e) {
      warn('转 wav 失败（保持原格式）：' + e.message);
    }
    return item;
  }

  // -------------------------------------------------------------------- zip
  var CRC_TABLE = (function () {
    var t = new Uint32Array(256);
    for (var n = 0; n < 256; n++) {
      var c = n;
      for (var k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
      t[n] = c >>> 0;
    }
    return t;
  })();
  function crc32(u8) {
    var c = 0xFFFFFFFF;
    for (var i = 0; i < u8.length; i++) c = CRC_TABLE[(c ^ u8[i]) & 0xFF] ^ (c >>> 8);
    return (c ^ 0xFFFFFFFF) >>> 0;
  }
  function makeZip(files) {
    var enc = new TextEncoder();
    var chunks = [], central = [], offset = 0;
    var now = new Date();
    var dtime = ((now.getHours() << 11) | (now.getMinutes() << 5) | (now.getSeconds() >> 1)) & 0xFFFF;
    var ddate = (((now.getFullYear() - 1980) << 9) | ((now.getMonth() + 1) << 5) | now.getDate()) & 0xFFFF;

    files.forEach(function (f) {
      var name = enc.encode(f.name);
      var data = f.data;
      var crc = crc32(data);
      var lh = new DataView(new ArrayBuffer(30));
      lh.setUint32(0, 0x04034b50, true);
      lh.setUint16(4, 20, true); lh.setUint16(6, 0, true); lh.setUint16(8, 0, true);
      lh.setUint16(10, dtime, true); lh.setUint16(12, ddate, true);
      lh.setUint32(14, crc, true);
      lh.setUint32(18, data.length, true); lh.setUint32(22, data.length, true);
      lh.setUint16(26, name.length, true); lh.setUint16(28, 0, true);
      chunks.push(new Uint8Array(lh.buffer), name, data);

      var ch = new DataView(new ArrayBuffer(46));
      ch.setUint32(0, 0x02014b50, true);
      ch.setUint16(4, 20, true); ch.setUint16(6, 20, true); ch.setUint16(8, 0, true);
      ch.setUint16(10, 0, true); ch.setUint16(12, dtime, true); ch.setUint16(14, ddate, true);
      ch.setUint32(16, crc, true);
      ch.setUint32(20, data.length, true); ch.setUint32(24, data.length, true);
      ch.setUint16(28, name.length, true); ch.setUint16(30, 0, true); ch.setUint16(32, 0, true);
      ch.setUint16(34, 0, true); ch.setUint16(36, 0, true); ch.setUint32(38, 0, true);
      ch.setUint32(42, offset, true);
      central.push(new Uint8Array(ch.buffer), name);

      offset += 30 + name.length + data.length;
    });

    var cdSize = central.reduce(function (s, x) { return s + x.length; }, 0);
    var eocd = new DataView(new ArrayBuffer(22));
    eocd.setUint32(0, 0x06054b50, true);
    eocd.setUint16(8, files.length, true); eocd.setUint16(10, files.length, true);
    eocd.setUint32(12, cdSize, true); eocd.setUint32(16, offset, true);
    return new Blob(chunks.concat(central, [new Uint8Array(eocd.buffer)]), { type: 'application/zip' });
  }

  function saveBlob(blob, name) {
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 4000);
  }

  function zipNow(name) {
    var tagged = CAP.audio.filter(function (a) { return a.hash; });
    if (!tagged.length) { warn('还没有已绑定句子的音频'); return; }
    var files = tagged.map(function (a) {
      return { name: a.hash + (a.kind === 'wav' ? '.wav' : '.' + a.kind), data: a.bytes };
    });
    var blob = makeZip(files);
    saveBlob(blob, name || ('voice_pack_' + files.length + '.zip'));
    log('已打包 ' + files.length + ' 个文件，' + (blob.size / 1048576).toFixed(1) + ' MB');
    var loose = CAP.audio.length - tagged.length;
    if (loose) warn('另有 ' + loose + ' 段未绑定句子的音频（诊断阶段采的），已跳过');
    var bad = tagged.filter(function (a) { return a.kind !== 'wav'; });
    if (bad.length) warn(bad.length + ' 个文件不是 wav，本机无 ffmpeg 无法转码');
  }

  // ---------------------------------------------------------------- 等待音频
  async function waitAudio(before, ms) {
    var t0 = Date.now();
    while (Date.now() - t0 < ms) {
      await sleep(250);
      if (CAP.audio.length > before) return CAP.audio[CAP.audio.length - 1];
      if (Date.now() - t0 > 1200) {
        await harvestFromElements();
        if (CAP.audio.length > before) return CAP.audio[CAP.audio.length - 1];
      }
    }
    return null;
  }

  // -------------------------------------------------------------------- 诊断
  async function probe(seconds, opt) {
    opt = opt || {};
    installHooks();
    seconds = seconds || 30;
    var input = findInput();
    var cands = findCandidates();
    ST.sel.input = input;
    ST.sel.btn = cands.length ? cands[0].el : null;

    console.log('%c===== DNACT 诊断 v' + VERSION + ' =====', 'color:#3aa0ff;font-weight:bold;font-size:13px');
    log('输入框：' + (input ? JSON.stringify(desc(input)) : '未找到'));
    log('合成按钮候选（前 5）：');
    if (cands.length) {
      console.table(cands.slice(0, 5).map(function (c) {
        return { text: c.text, cls: c.cls, score: Math.round(c.score) };
      }));
    } else {
      warn('没有匹配到「合成/生成」类按钮，将依赖「自动合成」或快捷键');
    }

    if (input) {
      var backup = readText(input);
      var strat = setText(input, '写入测试');
      var ok = readText(input).indexOf('写入测试') >= 0;
      log('写入测试：策略=' + strat + ' 回读=' + (ok ? '通过' : '失败') +
          ' 现内容="' + readText(input).slice(0, 20) + '"');
      setText(input, backup);
      await sleep(100);
      log('已尝试恢复原文本（若显示异常请手动改回）');
    }

    log('钩子已装。现在请手动完成一次合成（用「注意冰眼」），做完等 ' + seconds + ' 秒…');
    var base = CAP.audio.length;
    await sleep(seconds * 1000);
    await harvestFromElements();

    var got = CAP.audio.slice(base);
    console.log('%c===== 诊断结果 =====', 'color:#3aa0ff;font-weight:bold;font-size:13px');
    if (got.length) {
      log('★ 抓到 ' + got.length + ' 段音频：');
      console.table(got.map(function (a) {
        return {
          src: a.src, kind: a.kind, bytes: a.sz,
          head: Array.prototype.slice.call(a.bytes.slice(0, 4))
            .map(function (x) { return ('0' + x.toString(16)).slice(-2); }).join(' ')
        };
      }));
    } else {
      warn('没抓到音频字节（ws=' + CAP.wsCount + ' 文本响应=' + CAP.textLog.length +
           ' audio元素=' + CAP.elementCount + '）');
    }

    log('点击轨迹（最近 12 条）：');
    console.table(TRACE.clicks.slice(-12).map(function (c) {
      return { 元素: c.chain, 文本: c.text, aria: c.aria };
    }));
    if (TRACE.keys.length) {
      log('Enter 按键记录：');
      console.table(TRACE.keys.slice(-8));
    }
    log('请把以上输出（含点击轨迹表）整段复制给我。');
    return { audio: got.length, clicks: TRACE.clicks.length };
  }

  // ------------------------------------------------------------ 单句合成
  async function synthOne(text, idx, opt) {
    var input = ST.sel.input && ST.sel.input.isConnected ? ST.sel.input : findInput();
    if (!input) throw new Error('找不到输入框');

    var strat = setText(input, text);
    if (strat === 'failed') throw new Error('文本写入失败');
    await sleep(opt.writeDelay || 180);

    var before = CAP.audio.length;

    // 1) 先给「自动合成」一点时间
    var hit = await waitAudio(before, opt.autoWait || 6000);
    if (hit) { ST.auto++; return hit; }

    // 2) 试按钮候选
    var cands = findCandidates();
    for (var i = 0; i < Math.min(cands.length, 3); i++) {
      clickEl(cands[i].el);
      hit = await waitAudio(before, opt.btnWait || 12000);
      if (hit) { ST.click++; return hit; }
    }

    // 3) 试快捷键
    var combos = [{ key: 'Enter', ctrl: true }, { key: 'Enter', meta: true }, { key: 'Enter' }];
    for (var k = 0; k < combos.length; k++) {
      fireKey(input, combos[k]);
      hit = await waitAudio(before, opt.keyWait || 8000);
      if (hit) { ST.key++; return hit; }
    }
    return null;
  }

  // -------------------------------------------------------------------- 批量
  async function start(opt) {
    opt = opt || {};
    if (ST.running) { warn('正在运行，先 DNACT.stop()'); return; }
    installHooks();
    ST.running = true; ST.stop = false; ST.ok = 0; ST.fail = [];
    ST.auto = ST.click = ST.key = 0;

    var items = PHRASES.map(function (p, i) { return { text: p[0], hash: p[1], i: i }; });
    if (opt.from) items = items.filter(function (x) { return x.i >= opt.from - 1; });
    if (opt.only) items = items.filter(function (x) {
      return opt.only.some(function (t) { return x.text.indexOf(t) >= 0; });
    });
    if (opt.limit) items = items.slice(0, opt.limit);

    var input = findInput();
    ST.sel.input = input;
    ST.sel.btn = findSubmit();
    if (!input) { warn('找不到输入框，先 DNACT.probe()'); ST.running = false; return; }
    if (!ST.sel.btn) warn('未匹配到合成按钮，将依赖自动合成或快捷键');

    log('开始：' + items.length + ' 句。请确认页面音色已选好。');

    for (var k = 0; k < items.length; k++) {
      if (ST.stop) { warn('已中止，当前第 ' + (items[k].i + 1) + ' 句'); break; }
      var it = items[k];
      ST.cur = it.i;
      var t0 = Date.now();
      try {
        var hit = await synthOne(it.text, it.i, opt);
        if (hit) {
          await ensureWav(hit);
          hit.text = it.text;
          hit.hash = it.hash;
          ST.ok++;
          log('(' + (k + 1) + '/' + items.length + ') OK  ' + it.text + '  ' +
              hit.kind + ' ' + hit.sz + 'B  ' + ((Date.now() - t0) / 1000).toFixed(1) + 's');
        } else {
          ST.fail.push(it);
          warn('(' + (k + 1) + '/' + items.length + ') 无音频：' + it.text);
        }
      } catch (e) {
        ST.fail.push(it);
        warn('(' + (k + 1) + '/' + items.length + ') 异常 ' + it.text + ' → ' + e.message);
      }
      await sleep(opt.delay || 500);
    }

    ST.running = false;
    console.log('%c===== 批量结束 =====', 'color:#3aa0ff;font-weight:bold;font-size:13px');
    log('成功 ' + ST.ok + '/' + items.length + '，失败 ' + ST.fail.length +
        ' | 触发：自动 ' + ST.auto + ' / 按钮 ' + ST.click + ' / 快捷键 ' + ST.key);
    if (ST.fail.length) {
      console.table(ST.fail.map(function (f) { return { 序号: f.i + 1, 文案: f.text }; }));
      log('重跑失败项：DNACT.retry()');
    } else if (CAP.audio.length) {
      log('全部成功，打包中…');
      zipNow();
    }
  }

  async function retry(opt) {
    if (!ST.fail.length) { log('没有失败项'); return; }
    opt = opt || {};
    opt.only = ST.fail.map(function (f) { return f.text; });
    ST.fail = [];
    await start(opt);
  }

  function stop() { ST.stop = true; warn('收到中止信号'); }
  function status() {
    console.table({
      版本: VERSION, 运行中: ST.running, 当前句: ST.cur + 1,
      已采集: CAP.audio.length, 成功: ST.ok, 失败: ST.fail.length,
      自动: ST.auto, 按钮: ST.click, 快捷键: ST.key
    });
  }

  window.DNACT = {
    version: VERSION, PHRASES: PHRASES, CAP: CAP, ST: ST, TRACE: TRACE,
    probe: probe, start: start, retry: retry, stop: stop, status: status,
    zipNow: zipNow, findInput: findInput, findCandidates: findCandidates,
    setText: setText, readText: readText, transcodeToWav: transcodeToWav
  };

  log('v' + VERSION + ' 已加载，共 ' + PHRASES.length + ' 句，进入诊断…');
  probe(30);
})();
