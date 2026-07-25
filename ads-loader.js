// Ads Loader — reads ads.json and renders enabled slots (Auto-detect prefix)
(function() {
  'use strict';

  // ===== Auto-detect site prefix from existing ad-slot elements =====
  function detectPrefix() {
    var el = document.querySelector('[class$="-ad-slot"]');
    if (el) {
      var match = el.className.match(/([a-zA-Z0-9_-]+)-ad-slot/);
      if (match) return match[1];
    }
    // Fallback: try to find from any element with data-slot
    el = document.querySelector('[data-slot]');
    if (el) {
      var classes = el.className.split(' ');
      for (var i = 0; i < classes.length; i++) {
        if (classes[i].indexOf('-ad-slot') !== -1) {
          var parts = classes[i].split('-ad-slot');
          if (parts[0]) return parts[0];
        }
      }
    }
    return 'mct'; // ultimate fallback
  }

  var PREFIX = detectPrefix();
  var AD_SLOT_CLASS = PREFIX + '-ad-slot';
  var ARTICLE_GRID_CLASS = PREFIX + '-article-grid';
  var ARTICLE_CARD_CLASS = PREFIX + '-article-card';
  var BLOG_CARD_CLASS = PREFIX + '-blog-card';

  // Default config: all slots enabled (fallback for local file:// access)
  var DEFAULT_SLOTS = [
    {name:'hero_top',height:250,enabled:true},
    {name:'middle',height:250,enabled:true},
    {name:'footer',height:250,enabled:true},
    {name:'tool_top',height:250,enabled:true},
    {name:'tool_middle',height:250,enabled:true},
    {name:'tool_bottom',height:250,enabled:true},
    {name:'article_top',height:250,enabled:true},
    {name:'article_middle',height:250,enabled:true},
    {name:'article_bottom',height:250,enabled:true},
    {name:'blog_top',height:250,enabled:true},
    {name:'blog_mid_1',height:250,enabled:true},
    {name:'blog_mid_2',height:250,enabled:true},
    {name:'blog_mid_3',height:250,enabled:true},
    {name:'blog_mid_4',height:250,enabled:true},
    {name:'blog_mid_5',height:250,enabled:true},
    {name:'blog_mid_6',height:250,enabled:true},
    {name:'blog_mid_7',height:250,enabled:true},
    {name:'blog_mid_8',height:250,enabled:true},
    {name:'blog_mid_9',height:250,enabled:true},
    {name:'blog_mid_10',height:250,enabled:true},
    {name:'blog_mid_11',height:250,enabled:true},
    {name:'blog_mid_12',height:250,enabled:true},
    {name:'blog_mid_13',height:250,enabled:true},
    {name:'blog_mid_14',height:250,enabled:true},
    {name:'blog_mid_15',height:250,enabled:true},
    {name:'blog_mid_16',height:250,enabled:true},
    {name:'blog_mid_17',height:250,enabled:true},
    {name:'blog_mid_18',height:250,enabled:true},
    {name:'blog_mid_19',height:250,enabled:true},
    {name:'blog_mid_20',height:250,enabled:true},
    {name:'blog_mid_21',height:250,enabled:true},
    {name:'blog_mid_22',height:250,enabled:true},
    {name:'blog_mid_23',height:250,enabled:true},
    {name:'blog_mid_24',height:250,enabled:true},
    {name:'blog_mid_25',height:250,enabled:true},
    {name:'blog_mid_26',height:250,enabled:true},
    {name:'blog_mid_27',height:250,enabled:true},
    {name:'blog_mid_28',height:250,enabled:true},
    {name:'blog_mid_29',height:250,enabled:true},
    {name:'blog_mid_30',height:250,enabled:true},
    {name:'blog_bottom',height:250,enabled:true},
    {name:'about_bottom',height:250,enabled:true}
  ];

  function findSlotElement(slotName) {
    // Try id first, then data-slot with detected prefix
    var el = document.getElementById(slotName);
    if (el) return el;
    return document.querySelector('.' + AD_SLOT_CLASS + '[data-ad-slot="' + slotName + '"]');
  }

  // ===== 动态插入 blog_mid 广告位到正确位置 =====
  function insertBlogMidSlots(articleCount) {
    // 找文章容器
    var articleGrid = document.querySelector('.' + ARTICLE_GRID_CLASS) || document.getElementById('blog-articles');
    if (!articleGrid) return;

    // 获取所有文章卡片
    var articles = articleGrid.querySelectorAll('.' + ARTICLE_CARD_CLASS);
    if (articles.length === 0) {
      // Try blog-card class
      articles = articleGrid.querySelectorAll('.' + BLOG_CARD_CLASS);
    }
    if (articles.length === 0) return;

    // 对于 blog_mid_N (N>=2)，动态插入到第 N*9 篇文章后
    for (var n = 2; n <= 30; n++) {
      var threshold = n * 9;
      if (articleCount < threshold) break; // 文章不够，后面的也不需要了

      var slotName = 'blog_mid_' + n;

      // 检查是否已存在
      if (document.getElementById(slotName)) continue;

      // 在第 threshold 篇文章后插入
      var insertIndex = threshold - 1; // 0-based
      if (insertIndex >= articles.length) continue;

      var refNode = articles[insertIndex];
      var slotDiv = document.createElement('div');
      slotDiv.id = slotName;
      slotDiv.className = AD_SLOT_CLASS;
      slotDiv.setAttribute('data-ad-slot', slotName);
      slotDiv.innerHTML = '<div class="ad-placeholder">Blog Mid Ad — Loading...</div>';

      // 在 refNode 后面插入
      refNode.parentNode.insertBefore(slotDiv, refNode.nextSibling);
    }
  }

  function renderSlots(slots) {
    // 统计文章数量（通用匹配所有前缀的 article-card 和 blog-card）
    var articleCount = Array.from(document.querySelectorAll('[class*="-blog-card"], [class*="-article-card"]')).filter(function(el) {
      var c = el.className;
      return /(^|\s)[a-zA-Z0-9_-]+-blog-card(\s|$)/.test(c) || /(^|\s)[a-zA-Z0-9_-]+-article-card(\s|$)/.test(c);
    }).length;

    // 先动态插入 blog_mid 广告位（如果需要）
    insertBlogMidSlots(articleCount);

    slots.forEach(function(slot) {
      if (!slot.enabled) return;

      // blog_mid_N: 文章数 >= N*9 才显示
      var midMatch = slot.name.match(/^blog_mid_(\d+)$/);
      if (midMatch) {
        var threshold = parseInt(midMatch[1], 10) * 9;
        if (articleCount < threshold) return;
      }

      var el = findSlotElement(slot.name);
      if (!el) return;
      el.classList.add('has-content');
      el.innerHTML = '<div style="width:100%;height:' + slot.height + 'px;background:linear-gradient(135deg,#e0f2fe 0%,#bae6fd 100%);display:flex;align-items:center;justify-content:center;border-radius:8px;color:#0369a1;font-family:system-ui,sans-serif;font-size:14px;">Advertisement</div>';
    });
  }

  // Try to fetch ads.json, fallback to default on error (local file://)
  (function(){
    var scriptPath = document.currentScript ? document.currentScript.src : '';
    var basePath = '';
    if (scriptPath) {
      var lastSlash = scriptPath.lastIndexOf('/');
      if (lastSlash !== -1) {
        basePath = scriptPath.substring(0, lastSlash + 1);
      }
    }
    // Fallback for local file:// access: use relative path based on current page
    if (!basePath) {
      basePath = (location.pathname.includes('/blog/') || location.pathname.includes('/tools/') ? '../' : './');
    }
    return fetch(basePath + 'ads.json');
  })()
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data && data.slots && data.slots.length > 0) {
        renderSlots(data.slots);
      } else {
        renderSlots(DEFAULT_SLOTS);
      }
    })
    .catch(function() {
      // Local file:// access or network error — use default enabled slots
      renderSlots(DEFAULT_SLOTS);
    });
})();
