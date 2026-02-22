(function(){
  'use strict';

  function qs(id){ return document.getElementById(id); }

  var widget = qs('ai-help-widget');
  var fab = qs('ai-help-fab');
  var panel = qs('ai-help-panel');
  var closeBtn = qs('ai-close');
  var body = qs('ai-body');
  var messages = qs('ai-messages');
  var scan = qs('ai-scan');
  var input = qs('ai-input');
  var sendBtn = qs('ai-send');
  var USER_ID = (typeof window !== 'undefined' && window.__USER_ID) ? window.__USER_ID : null;

  function hideWidget(){ if(widget) widget.style.display = 'none'; }
  function showWidget(){ if(widget) widget.style.display = ''; }

  function fetchJson(url, opts){
    return fetch(url, opts).then(function(r){ if(!r.ok) throw r; return r.json(); });
  }

  function setAriaOpen(open){
    if(panel){ panel.style.display = open ? 'flex' : 'none'; panel.setAttribute('aria-hidden', open ? 'false' : 'true'); }
    if(widget) widget.setAttribute('aria-hidden', open ? 'false' : 'true');
  }

  function clearMessages(){ if(messages) messages.innerHTML = ''; }

  function appendBotCard(html){
    var div = document.createElement('div');
    div.className = 'ai-msg bot';
    div.innerHTML = html;
    messages.appendChild(div);
    messages.scrollTop = messages.scrollHeight;
    return div;
  }

  function appendUser(text){
    var d = document.createElement('div'); d.className = 'ai-msg user'; d.textContent = text; messages.appendChild(d); messages.scrollTop = messages.scrollHeight; }

  function showTyping(){
    var d = document.createElement('div'); d.className = 'ai-msg bot typing'; d.innerHTML = '<span class="typing"><span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span></span>';
    messages.appendChild(d); messages.scrollTop = messages.scrollHeight; return d;
  }

  function appendBotText(text){
    var d = document.createElement('div'); d.className = 'ai-msg bot'; d.textContent = text; messages.appendChild(d); messages.scrollTop = messages.scrollHeight; return d;
  }

  function renderOptions(opts, nodeId){
    var wrap = document.createElement('div'); wrap.className = 'ai-card';
    var container = document.createElement('div'); container.style.display = 'flex'; container.style.flexWrap = 'wrap'; container.style.gap = '8px';
    opts.forEach(function(o){
      var btn = document.createElement('button'); btn.className = 'qa'; btn.textContent = o.option_text; btn.dataset.optionId = o.id; if(o.next_node_id) btn.dataset.next = o.next_node_id; if(o.action_type) btn.dataset.actionType = o.action_type; if(o.action_payload) btn.dataset.actionPayload = o.action_payload;
      btn.addEventListener('click', function(){ handleOptionClick(nodeId, o); });
      container.appendChild(btn);
    });
    wrap.appendChild(container);
    appendBotCard(wrap.outerHTML);
  }

  function handleOptionClick(nodeId, option){
    appendUser(option.option_text || '');
    // optimistic log (include session user id when available)
    fetch('/assistant/log', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({node_id: nodeId, option_id: option.id, user_id: USER_ID, metadata: null})}).catch(function(){});

    // perform action if present
    if(option.action_type){
      if(option.action_type === 'link' && option.action_payload){ window.open(option.action_payload, '_blank'); }
      else if(option.action_type === 'goto' && option.action_payload){ window.location.href = option.action_payload; }
      else if(option.action_type === 'contact'){ appendBotCard('<div class="ai-card"><h4>Contact</h4><p>Please reach out to the admin via the support page.</p></div>'); }
    }

    if(option.next_node_id){
      var t = showTyping();
      fetchJson('/assistant/node/' + option.next_node_id).then(function(res){
        if(t && t.parentNode) t.parentNode.removeChild(t);
        if(res.node && res.node.question) appendBotCard('<div class="ai-card"><h4>' + escapeHtml(res.node.question) + '</h4></div>');
        if(Array.isArray(res.options) && res.options.length) renderOptions(res.options, res.node.id);
      }).catch(function(){ if(t && t.parentNode) t.parentNode.removeChild(t); appendBotCard('<div class="ai-card"><h4>Oops</h4><p>Failed to load response.</p></div>'); });
    }
  }

  function escapeHtml(s){ return String(s).replace(/[&<>"']/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }

  function startConversation(){
    clearMessages();
    // try database-driven assistant first, fall back to scripted greeting
    scan && (scan.style.display = 'block');
    fetchJson('/assistant/start').then(function(res){
      scan && (scan.style.display = 'none');
      if(res.error){
        showGreeting();
        return;
      }
      if(res.node && res.node.question) appendBotCard('<div class="ai-card"><h4>' + escapeHtml(res.node.question) + '</h4></div>');
      if(Array.isArray(res.options) && res.options.length) renderOptions(res.options, res.node.id);
    }).catch(function(){ scan && (scan.style.display = 'none'); showGreeting(); });
  }

  function showGreeting(){
    clearMessages();
    appendBotCard('<div class="ai-card"><h4>Welcome 👋</h4><p>I\'m the assistant for InvestPro. I can help you understand the program, show plans, share success stories, and guide you step-by-step.</p></div>');
    // render quick action buttons inside messages area for discoverability
    var wrap = document.createElement('div'); wrap.className = 'ai-card';
    var btns = [
      {t:'📊 View Investment Plans', a:'view-plans'},
      {t:'⭐ Success Stories', a:'stories'},
      {t:'ℹ️ How It Works', a:'how'},
      {t:'📞 Contact Admin', a:'contact'},
      {t:'💬 WhatsApp Support', a:'whatsapp'}
    ];
    var container = document.createElement('div'); container.style.display = 'flex'; container.style.flexWrap = 'wrap'; container.style.gap = '8px';
    btns.forEach(function(b){ var btn = document.createElement('button'); btn.className = 'qa'; btn.textContent = b.t; btn.dataset.action = b.a; btn.addEventListener('click', quickActionHandler); container.appendChild(btn); });
    wrap.appendChild(container);
    appendBotCard(wrap.outerHTML);
  }

  function quickActionHandler(e){
    var action = e.currentTarget.dataset.action;
    if(!action) return;
    if(action === 'view-plans'){
      fetchJson('/assistant/plans').then(function(res){
        if(Array.isArray(res.plans) && res.plans.length){
          clearMessages();
          appendBotCard('<div class="ai-card"><h4>Available Plans</h4></div>');
          res.plans.forEach(function(p){ appendBotCard('<div class="ai-card"><h4>' + escapeHtml(p.plan_name) + '</h4><p>Amount: $' + (p.minimum_amount||0) + ' • Benefit: ' + escapeHtml(String(p.profit_amount||p.total_return||'')) + '</p><p><a href="/plans/' + encodeURIComponent(p.id) + '">View plan</a></p></div>'); });
        } else {
          appendBotCard('<div class="ai-card"><h4>No Plans</h4><p>No plans are currently available.</p></div>');
        }
      }).catch(function(){ appendBotCard('<div class="ai-card"><h4>Error</h4><p>Could not load plans.</p></div>'); });
    } else if(action === 'stories'){
      fetchJson('/assistant/testimonials').then(function(res){
        clearMessages(); appendBotCard('<div class="ai-card"><h4>Success Stories</h4></div>');
        (res.testimonials||[]).forEach(function(t){ appendBotCard('<div class="ai-card"><h4>' + escapeHtml(t.title) + '</h4><p>' + escapeHtml(t.body) + '</p></div>'); });
      }).catch(function(){ appendBotCard('<div class="ai-card"><h4>Error</h4><p>Could not load testimonials.</p></div>'); });
    } else if(action === 'how'){
      fetchJson('/assistant/info').then(function(res){ clearMessages(); appendBotCard('<div class="ai-card"><h4>How It Works</h4><p>' + escapeHtml(res.description || '') + '</p></div>'); }).catch(function(){ appendBotCard('<div class="ai-card"><h4>How It Works</h4><p>Choose a plan, activate your account, and monitor your dashboard.</p></div>'); });
    } else if(action === 'contact'){
      fetchJson('/assistant/contact').then(function(res){ clearMessages(); appendBotCard('<div class="ai-card"><h4>Admin Contact</h4><p>Name: ' + escapeHtml(res.name||'Admin') + '<br>Phone: ' + escapeHtml(res.phone||'') + '</p></div>'); }).catch(function(){ appendBotCard('<div class="ai-card"><h4>Contact</h4><p>Please contact the admin via the contact page.</p></div>'); });
    } else if(action === 'whatsapp'){
      fetchJson('/assistant/contact').then(function(res){ if(res.whatsapp){ window.open(res.whatsapp, '_blank'); } else { appendBotCard('<div class="ai-card"><h4>WhatsApp</h4><p>WhatsApp contact is not configured.</p></div>'); } }).catch(function(){ appendBotCard('<div class="ai-card"><h4>WhatsApp</h4><p>Could not fetch contact.</p></div>'); });
    }
  }

  function sendMessage(text){
    if(!text || !text.trim()) return;
    appendUser(text);
    var t = showTyping();
    fetchJson('/assistant/query', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({message: text, user_id: USER_ID})}).then(function(res){
      if(t && t.parentNode) t.parentNode.removeChild(t);
      if(res && res.reply){ appendBotText(res.reply); }
      else appendBotText('No reply received.');
    }).catch(function(){ if(t && t.parentNode) t.parentNode.removeChild(t); appendBotText('Failed to contact assistant.'); });
  }

  function fetchConfig(){
    fetchJson('/assistant/config').then(function(cfg){
      if(!cfg.enabled){ hideWidget(); }
      else showWidget();
    }).catch(function(){ hideWidget(); });
  }

  // auto prompt after 30s if user hasn't opened assistant
  var _autoPromptTimer = null;
  function scheduleAutoPrompt(){
    if(_autoPromptTimer) clearTimeout(_autoPromptTimer);
    _autoPromptTimer = setTimeout(function(){
      if(panel && panel.style.display !== 'flex'){
        setAriaOpen(true);
        showGreeting();
      }
    }, 30000);
  }
  // reset auto prompt on common interactions
  ['click','keydown','mousemove','scroll','touchstart'].forEach(function(ev){ document.addEventListener(ev, scheduleAutoPrompt, {passive:true}); });
  scheduleAutoPrompt();

  // wire up UI
  document.addEventListener('DOMContentLoaded', function(){
    if(!widget) return;
    // initial config
    fetchConfig();

    fab && fab.addEventListener('click', function(e){ setAriaOpen(true); startConversation(); body && body.focus(); });
    closeBtn && closeBtn.addEventListener('click', function(){ setAriaOpen(false); });

    // send message handlers
    if(sendBtn){ sendBtn.addEventListener('click', function(){ if(input) { sendMessage(input.value); input.value = ''; } }); }
    if(input){ input.addEventListener('keydown', function(e){ if(e.key === 'Enter'){ e.preventDefault(); if(sendBtn) sendBtn.click(); } }); }

    // quick-actions in footer: delegate to unified quickActionHandler
    var qas = panel ? panel.querySelectorAll('.quick-actions [data-action]') : [];
    Array.prototype.forEach.call(qas, function(b){ b.addEventListener('click', quickActionHandler); });

    // close when clicking outside panel
    document.addEventListener('click', function(e){ if(!panel.contains(e.target) && !fab.contains(e.target) && panel.style.display === 'flex'){ setAriaOpen(false); } });
  });

})();
