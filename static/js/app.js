// Minimal JS: nav toggle and basic accessibility behaviors
document.addEventListener('DOMContentLoaded', function(){
  var btn = document.getElementById('nav-toggle');
  var nav = document.getElementById('primary-nav');
  if(btn && nav){
    btn.addEventListener('click', function(){
      var expanded = btn.getAttribute('aria-expanded') === 'true';
      btn.setAttribute('aria-expanded', String(!expanded));
      if(!expanded){
        nav.style.display = 'block';
      } else {
        nav.style.display = 'none';
      }
    });
  }

  // Plan invest form validation and confirmation modal
  var investForm = document.querySelector('form[action="/invest"]');
  if(investForm){
    var input = investForm.querySelector('input[name="local_amount"]');
    var errorBox = document.getElementById('local_amount_error');
    var modal = document.getElementById('confirmModal');
    var confirmBody = document.getElementById('confirmBody');
    var confirmOk = document.getElementById('confirmOk');
    var confirmCancel = document.getElementById('confirmCancel');
    investForm.addEventListener('submit', function(e){
      e.preventDefault();
      var val = parseFloat(input.value || '0');
      var minLocal = parseFloat(input.getAttribute('data-min-local')) || 0;
      var maxLocal = parseFloat(input.getAttribute('data-max-local')) || null;
      var rate = parseFloat(input.getAttribute('data-rate')) || 0;
      var symbol = input.getAttribute('data-currency-symbol') || '';
      // enforce min
      if(minLocal && val < minLocal){
        if(errorBox){ errorBox.style.display='block'; errorBox.textContent = 'Minimum amount is ' + symbol + minLocal.toFixed(2); }
        return;
      }
      // enforce max if present
      if(maxLocal && val > maxLocal){
        if(errorBox){ errorBox.style.display='block'; errorBox.textContent = 'Maximum amount is ' + symbol + maxLocal.toFixed(2); }
        return;
      }
      if(errorBox){ errorBox.style.display='none'; errorBox.textContent = ''; }
      // compute approx USD using rate if available
      var approxUsd = null;
      if(rate && rate > 0){ approxUsd = val / rate; }
      var html = '<p>Local amount: <strong>' + symbol + val.toFixed(2) + '</strong></p>';
      if(approxUsd !== null){ html += '<p>Approx. in USD: <strong>$' + approxUsd.toFixed(2) + '</strong></p>'; }
      else { html += '<p><em>USD conversion unavailable — server will compute accurate value.</em></p>'; }
      html += '<p>Plans are defined in USD; this action will create a pending investment recorded in USD.</p>';
      if(confirmBody) confirmBody.innerHTML = html;
      if(modal) modal.style.display = 'flex';
      // wire confirm
      confirmOk.onclick = function(){
        modal.style.display = 'none';
        investForm.submit();
      };
      confirmCancel.onclick = function(){ modal.style.display = 'none'; };
    });
  }
  // Countdown timers for plan cards
  function pad(n){return n<10?'0'+n:n}
  function formatRemaining(ms){
    if(ms<=0) return '00:00:00';
    var s=Math.floor(ms/1000);
    var days=Math.floor(s/86400); s%=86400;
    var hrs=Math.floor(s/3600); s%=3600;
    var mins=Math.floor(s/60); var secs=s%60;
    if(days>0) return days+'d '+pad(hrs)+':'+pad(mins)+':'+pad(secs);
    return pad(hrs)+':'+pad(mins)+':'+pad(secs);
  }
  var cds = document.querySelectorAll('.countdown[data-end]');
  if(cds.length){
    function tick(){
      var now = new Date();
      cds.forEach(function(el){
        var end = el.getAttribute('data-end');
        var endDate = new Date(end);
        if(isNaN(endDate)){
          // try numeric seconds
          var secs = parseInt(end,10);
          if(!isNaN(secs)) endDate = new Date(Date.now() + secs*1000);
        }
        var rem = endDate - now;
        var span = el.querySelector('.countdown-timer');
        if(rem <= 0){
          span.textContent = 'Offer ended';
        } else {
          span.textContent = formatRemaining(rem);
        }
      });
    }
    tick();
    setInterval(tick,1000);
  }

  // admin dropdown toggle for small screens
  var adminToggle = document.getElementById('admin-toggle');
  var adminMenu = document.getElementById('admin-menu');
  if(adminToggle && adminMenu){
    adminToggle.addEventListener('click', function(e){
      e.preventDefault();
      var open = adminToggle.getAttribute('aria-expanded') === 'true';
      adminToggle.setAttribute('aria-expanded', String(!open));
      adminMenu.style.display = open ? 'none' : 'block';
    });
    // close when clicking outside
    document.addEventListener('click', function(ev){
      if(!adminMenu.contains(ev.target) && ev.target !== adminToggle){
        adminMenu.style.display = 'none';
        adminToggle.setAttribute('aria-expanded','false');
      }
    });
  }

  // Animated counters for plan cards (investors, views, rating)
  function formatNumber(n){
    if(n === null || n === undefined) return '0';
    var num = Number(n);
    if(isNaN(num)) return String(n);
    // large number formatting (1,234,567)
    return num.toLocaleString();
  }

  function animateValue(el, start, end, duration, decimals){
    var startTime = null; decimals = decimals||0;
    function step(ts){
      if(!startTime) startTime = ts;
      var progress = Math.min((ts - startTime) / duration, 1);
      var value = start + (end - start) * progress;
      if(decimals>0) el.textContent = value.toFixed(decimals);
      else el.textContent = Math.round(value).toLocaleString();
      if(progress < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  var counted = new WeakSet();
  var liveIntervals = new WeakMap();

  function getRandomInt(min, max){ return Math.floor(Math.random()*(max-min+1))+min; }

  function startLiveIncrementForCount(el){
    if(liveIntervals.has(el)) return;
    var id = setInterval(function(){
      var cur = Number(String(el.textContent).replace(/,/g,'')) || 0;
      var inc = 1;
      if(cur < 100) inc = getRandomInt(1,5);
      else if(cur < 1000) inc = getRandomInt(5,20);
      else inc = Math.max(1, Math.round(cur * 0.002));
      cur = cur + inc;
      el.textContent = cur.toLocaleString();
    }, 5000);
    liveIntervals.set(el, id);
  }

  function startLiveIncrementForRating(el){
    if(liveIntervals.has(el)) return;
    var id = setInterval(function(){
      var cur = parseFloat(el.textContent) || 0;
      var inc = (Math.random() * 0.2); // small bump
      cur = Math.min(5, +(cur + inc).toFixed(1));
      el.textContent = cur.toFixed(1);
    }, 5000);
    liveIntervals.set(el, id);
  }

  var observer = new IntersectionObserver(function(entries){
    entries.forEach(function(entry){
      if(!entry.isIntersecting) return;
      var root = entry.target;
      // investors and views
      var counters = root.querySelectorAll('.count-anim');
      counters.forEach(function(c){
        if(counted.has(c)) return;
        var target = Number(c.getAttribute('data-target')) || 0;
        animateValue(c, 0, target, 1200, 0);
        counted.add(c);
        // start periodic live increments after initial animation
        startLiveIncrementForCount(c);
      });
      // rating (decimal)
      var r = root.querySelector('.rating-anim');
      if(r && !counted.has(r)){
        var rt = parseFloat(r.getAttribute('data-target')) || 0;
        animateValue(r, 0, rt, 900, 1);
        counted.add(r);
        startLiveIncrementForRating(r);
      }
      observer.unobserve(root);
    });
  }, {threshold: 0.35});

  // observe each plan-card-detailed
  var plans = document.querySelectorAll('.plan-card-detailed');
  plans.forEach(function(p){ observer.observe(p); });
});

/* Live testimonials / activity ticker */
document.addEventListener('DOMContentLoaded', function(){
  var ticker = document.getElementById('live-ticker');
  var msgEl = document.getElementById('ticker-message');
  var closeBtn = document.getElementById('ticker-close');
  if(!ticker || !msgEl) return;

  // respect previously dismissed state
  try{ if(localStorage.getItem('liveTickerHidden') === '1'){ ticker.style.display='none'; } }catch(e){}

  function randomFrom(arr){ return arr[Math.floor(Math.random()*arr.length)]; }

  function generateMessages(n){
    var names = ['John','Michael','Aisha','Fatima','Sarah','David','Emma','Chris','Olivia','Noah','Liam','Sophia','Grace','Samuel','Daniel','Maya','Alex','Abdul','Karen','Hassan'];
    var countries = ['US','UK','NG','CA','GH','KE','ZA','AU','IN','NG','IE','US','CA','DE','FR'];
    var actions = [
      'just registered',
      'made a deposit of {amount}',
      'withdrawal successful for {amount}',
      'completed KYC',
      'purchased a plan',
      'upgraded to a higher plan',
      'received payout of {amount}'
    ];
    var currencies = ['$', '₦', '€'];
    var msgs = [];
    for(var i=0;i<n;i++){
      var name = randomFrom(names);
      var country = randomFrom(countries);
      var action = randomFrom(actions);
      var amount = '';
      if(action.indexOf('{amount}') !== -1){
        var cur = randomFrom(currencies);
        var val = Math.floor(Math.random()*9500)+50; // 50 - 9550
        // make larger numbers prettier
        if(val>999) val = (Math.round(val/100)*100);
        amount = cur + val.toLocaleString();
        action = action.replace('{amount}', amount);
      }
      msgs.push(name + ' from ' + country + ' ' + action + '.');
    }
    return msgs;
  }

  var messages = generateMessages(30);
  var idx = 0;
  var interval = 3000;
  var animTimeout = null;

  function showNext(){
    if(!msgEl) return;
    // fade out
    msgEl.classList.remove('show');
    msgEl.classList.add('fade');
    clearTimeout(animTimeout);
    animTimeout = setTimeout(function(){
      msgEl.textContent = messages[idx];
      msgEl.classList.remove('fade');
      msgEl.classList.add('show');
      idx = (idx + 1) % messages.length;
    }, 260);
  }

  var tickerInterval = setInterval(showNext, interval);
  // show first immediately
  msgEl.textContent = messages[0];
  msgEl.classList.add('show');
  idx = 1;

  // pause on hover
  ticker.addEventListener('mouseenter', function(){ clearInterval(tickerInterval); });
  ticker.addEventListener('mouseleave', function(){ tickerInterval = setInterval(showNext, interval); });

  if(closeBtn){
    closeBtn.addEventListener('click', function(e){
      e.preventDefault();
      ticker.style.display = 'none';
      try{ localStorage.setItem('liveTickerHidden','1'); }catch(e){}
    });
  }
});
