// Reads the words in a picture with Apple's Vision framework, from JavaScript for Automation:
//   osascript -l JavaScript vision_ocr.js PICTURE
// It is built into macOS, free and offline. handbook/ocr.py runs it and turns what it prints into text.
//
// A picture up to about 2,600 pixels across is read whole. A bigger one (a menu poster 4,600 pixels
// wide and 13,000 tall, a photographed page) is read twice, in different cuts, because Vision shrinks
// a large picture before it looks and then loses the small print: across a 4,480 x 8,600 allergen
// table one whole-picture read found 56 pieces of text and the cuts found about 1,500. Pass 1 cuts
// the full width into bands 800 pixels tall; pass 2 cuts tiles 1,800 wide and 600 tall. Each piece of
// text comes back with its box in whole-picture pixels (top-left origin), so ocr.py can join the
// passes and set the words back into rows.
//
// Prints one line of JSON: {"w", "h", "passes": [[{"t", "c", "l", "r", "top", "bot"}, ...], ...]}
// or {"error": "..."}.

ObjC.import('Vision');
ObjC.import('ImageIO');
ObjC.import('CoreGraphics');
ObjC.import('Foundation');

var WHOLE_MAX = 2600;

function readTile(image, W, H, x, y, w, h, out) {
  var whole = (w === W && h === H);
  var tile = whole ? image : $.CGImageCreateWithImageInRect(image, {origin: {x: x, y: y}, size: {width: w, height: h}});
  var handler = $.VNImageRequestHandler.alloc.initWithCGImageOptions(tile, $({}));
  var req = $.VNRecognizeTextRequest.alloc.init;
  req.recognitionLevel = 0;              // accurate
  req.usesLanguageCorrection = true;
  try { req.automaticallyDetectsLanguage = true; } catch (e) { /* older macOS: English only */ }
  var ok = handler.performRequestsError($([req]), $());
  if (!ok) return;
  var res = req.results;
  for (var i = 0; i < res.count; i++) {
    var o = res.objectAtIndex(i);
    var c = o.topCandidates(1);
    if (c.count == 0) continue;
    var cand = c.objectAtIndex(0);
    var bb = o.boundingBox;              // normalised, origin bottom-left
    var top = y + (1 - (bb.origin.y + bb.size.height)) * h;
    out.push({t: ObjC.unwrap(cand.string), c: cand.confidence,
              l: x + bb.origin.x * w, r: x + (bb.origin.x + bb.size.width) * w,
              top: top, bot: top + bb.size.height * h});
  }
}

function readTiles(image, W, H, tw, th, overlap) {
  var out = [];
  var y = 0;
  while (y < H) {
    var h = Math.min(th, H - y);
    var x = 0;
    while (x < W) {
      var w = Math.min(tw, W - x);
      readTile(image, W, H, x, y, w, h, out);
      if (x + w >= W) break;
      x += w - overlap;
    }
    if (y + h >= H) break;
    y += h - overlap;
  }
  return out;
}

function run(argv) {
  var url = $.NSURL.fileURLWithPath(argv[0]);
  var src = $.CGImageSourceCreateWithURL(url, $());
  if (!src || $.CGImageSourceGetCount(src) < 1) return JSON.stringify({error: 'unreadable'});
  // The thumbnail call applies the picture's own rotation (a phone photo's EXIF orientation) and
  // reads HEIC, TIFF, WebP, GIF, PNG and JPEG alike.
  var opts = $.NSMutableDictionary.alloc.init;
  opts.setObjectForKey($(true), $.kCGImageSourceCreateThumbnailFromImageAlways);
  opts.setObjectForKey($(true), $.kCGImageSourceCreateThumbnailWithTransform);
  opts.setObjectForKey($(20000), $.kCGImageSourceThumbnailMaxPixelSize);
  var image = $.CGImageSourceCreateThumbnailAtIndex(src, 0, opts);
  if (!image) return JSON.stringify({error: 'unreadable'});
  var W = $.CGImageGetWidth(image), H = $.CGImageGetHeight(image);
  var passes = [];
  if (Math.max(W, H) <= WHOLE_MAX) {
    passes.push(readTiles(image, W, H, W, H, 0));
  } else {
    passes.push(readTiles(image, W, H, W, 800, 80));
    passes.push(readTiles(image, W, H, 1800, 600, 100));
  }
  return JSON.stringify({w: W, h: H, passes: passes});
}
