// NovelDB apps landing page: theme switch, screenshot tabs, lightbox, and the latest release's files.
(function () {
  "use strict";

  var root = document.documentElement;
  var REPO = "Shirochi-stack/NovelDB";

  // Theme: follows the system until the button picks one; the pick is remembered.
  function isDark() {
    if (root.dataset.theme) return root.dataset.theme === "dark";
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  }
  document.getElementById("themeToggle").addEventListener("click", function () {
    var next = isDark() ? "light" : "dark";
    root.dataset.theme = next;
    try { localStorage.setItem("noveldb-apps-theme", next); } catch (e) {}
  });

  // Screenshot tabs.
  document.querySelectorAll(".shots-tabs").forEach(function (list) {
    var tabs = list.querySelectorAll("[role=tab]");
    tabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        tabs.forEach(function (other) {
          var on = other === tab;
          other.setAttribute("aria-selected", on ? "true" : "false");
          document.getElementById(other.getAttribute("aria-controls")).hidden = !on;
        });
      });
    });
  });

  // Lightbox for screenshots.
  var box = document.getElementById("lightbox");
  var boxImg = box.querySelector("img");
  document.addEventListener("click", function (event) {
    var img = event.target.closest && event.target.closest("img.zoomable");
    if (!img) return;
    boxImg.src = img.currentSrc || img.src;
    boxImg.alt = img.alt;
    box.hidden = false;
  });
  box.addEventListener("click", function () { box.hidden = true; });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") box.hidden = true;
  });

  // The visitor's platform, for the hero button.
  function platform() {
    var ua = navigator.userAgent || "";
    if (/android/i.test(ua)) return "android";
    if (/iphone|ipad|ipod/i.test(ua) || (/macintosh/i.test(ua) && navigator.maxTouchPoints > 1)) return "ios";
    if (/windows/i.test(ua)) return "windows";
    if (/mac os x|macintosh/i.test(ua)) return "macos";
    if (/linux|x11/i.test(ua)) return "linux";
    return "";
  }

  function size(bytes) {
    return bytes >= 1e9 ? (bytes / 1e9).toFixed(1) + " GB" : Math.round(bytes / 1e6) + " MB";
  }

  // Desktop assets carry the build number in their names (NovelDB105-windows-x86_64.exe), so their
  // links come from the latest release; without the API the cards keep their Releases link.
  var DESKTOP = {
    windows: [[/^NovelDB\d+-windows-x86_64\.exe$/, "Full (.exe)"], [/^NovelDB\d+-windows-x86_64\.zip$/, "Full, folder (.zip, starts faster)"],
              [/^NovelDB\d+_Lite-windows/, "Lite (.exe)"], [/^NovelDB\d+_TurboLite-windows/, "TurboLite (.exe)"]],
    macos: [[/^NovelDB\d+-macos-arm64/, "Full, Apple silicon"], [/^NovelDB\d+-macos-x86_64/, "Full, Intel"],
            [/^NovelDB\d+_Lite-macos-arm64/, "Lite, Apple silicon"], [/^NovelDB\d+_Lite-macos-x86_64/, "Lite, Intel"],
            [/^NovelDB\d+_TurboLite-macos-arm64/, "TurboLite, Apple silicon"], [/^NovelDB\d+_TurboLite-macos-x86_64/, "TurboLite, Intel"]],
    linux: [[/^NovelDB\d+-linux/, "Full (.AppImage)"], [/^NovelDB\d+_Lite-linux/, "Lite (.AppImage)"],
            [/^NovelDB\d+_TurboLite-linux/, "TurboLite (.AppImage)"]]
  };

  function link(asset, label) {
    var a = document.createElement("a");
    a.href = asset.browser_download_url;
    a.textContent = label + " ";
    var small = document.createElement("small");
    small.textContent = size(asset.size);
    a.appendChild(small);
    return a;
  }

  fetch("https://api.github.com/repos/" + REPO + "/releases/latest", { headers: { Accept: "application/vnd.github+json" } })
    .then(function (response) { return response.ok ? response.json() : Promise.reject(response.status); })
    .then(function (release) {
      var assets = release.assets || [];
      document.querySelectorAll("[data-release=tag]").forEach(function (el) { el.textContent = release.name || release.tag_name; });
      document.querySelectorAll("[data-size]").forEach(function (el) {
        var asset = assets.filter(function (a) { return a.name === el.dataset.size; })[0];
        if (asset) el.textContent = size(asset.size);
      });
      Object.keys(DESKTOP).forEach(function (os) {
        var box = document.querySelector('.files[data-os="' + os + '"]');
        var links = [];
        DESKTOP[os].forEach(function (pair) {
          var asset = assets.filter(function (a) { return pair[0].test(a.name); })[0];
          if (asset) links.push(link(asset, pair[1]));
        });
        if (!box || !links.length) return;
        box.textContent = "";
        links.forEach(function (a) { box.appendChild(a); });
      });
      var hero = document.getElementById("heroDownload");
      var os = platform();
      var first = os && document.querySelector('.files[data-os="' + os + '"] a');
      var labels = { windows: "Download for Windows", macos: "Download for macOS", linux: "Download for Linux",
                     android: "Download for Android", ios: "Download for iOS" };
      if (os === "android" || os === "ios") {
        first = document.querySelector('.files a[href$="' + (os === "android" ? ".apk" : ".ipa") + '"]');
      }
      if (first) {
        hero.href = first.href;
        hero.querySelector("span").textContent = labels[os];
      }
    })
    .catch(function () { /* offline or rate limited: the Releases links stay */ });
})();
