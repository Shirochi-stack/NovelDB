/**
 * reader_gm_patch.js - runs after gm_stubs.js, before rules-lib.js.
 *
 * Rules download images (attachments) as blobs through GM_xmlhttpRequest.
 * The iOS pipeline downloads images itself from the URLs in the chapter
 * HTML, so blob requests get an empty image instead of a network round
 * trip through JavaScript. Set __npia_deferBlobs = false to download them.
 */
(function (global) {
  "use strict";

  var original = global.GM_xmlhttpRequest;
  global.__npia_deferBlobs = true;

  global.GM_xmlhttpRequest = function (details) {
    if (!global.__npia_deferBlobs || !details || details.responseType !== "blob") return original(details);
    var url = details.url;
    Promise.resolve().then(function () {
      var ext = /\.(png|gif|webp|avif|bmp|svg)(?:[?#]|$)/i.exec(String(url));
      var type = "image/" + (ext ? ext[1].toLowerCase().replace("svg", "svg+xml") : "jpeg");
      if (details.onload) {
        details.onload({
          status: 200,
          statusText: "OK",
          responseText: "",
          response: new Blob([], { type: type }),
          readyState: 4,
          responseHeaders: "content-type: " + type + "\r\n",
          finalUrl: url,
        });
      }
    });
    return { abort: function () {} };
  };
  global.GM.xmlHttpRequest = global.GM_xmlhttpRequest;
})(this);
