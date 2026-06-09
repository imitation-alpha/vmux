/* Math-curve loaders for vmux. Parametric curves + fading particle trail, drawn
   as inline SVG and driven by a single shared requestAnimationFrame loop.

   Inspired by https://github.com/Paidax01/math-curve-loaders (reimplemented from
   the public parametric formulas; no source code copied). */
(function () {
  "use strict";
  var TAU = Math.PI * 2;
  var SVGNS = "http://www.w3.org/2000/svg";

  // Each curve maps t in [0,1] and a "detail" breath scale to a point in the
  // 0..100 viewBox (centered on 50,50). Labels match the picker in Settings.
  var CURVES = [
    {
      key: "original", label: "Original Thinking",
      point: function (t, d) {
        var a = t * TAU;
        return {
          x: 50 + 24 * Math.cos(a) - 10 * d * Math.cos(7 * a),
          y: 50 + 24 * Math.sin(a) - 10 * d * Math.sin(7 * a),
        };
      },
    },
    {
      key: "rose", label: "Rose",
      point: function (t, d) {
        var a = t * TAU, r = 36 * (0.72 + 0.28 * d) * Math.cos(5 * a);
        return { x: 50 + Math.cos(a) * r, y: 50 + Math.sin(a) * r };
      },
    },
    {
      key: "lissajous", label: "Lissajous",
      point: function (t, d) {
        var a = t * TAU, A = 36 * (0.8 + 0.2 * d);
        return { x: 50 + A * Math.sin(3 * a + Math.PI / 2), y: 50 + A * Math.sin(2 * a) };
      },
    },
    {
      key: "lemniscate", label: "Lemniscate",
      point: function (t, d) {
        var a = t * TAU, s = Math.sin(a), c = Math.cos(a), den = 1 + s * s,
            k = 40 * (0.8 + 0.2 * d);
        return { x: 50 + (k * c) / den, y: 50 + (k * s * c) / den };
      },
    },
    {
      key: "hypotrochoid", label: "Hypotrochoid",
      point: function (t, d) {
        var a = t * TAU * 3, R = 5, r = 3, dd = 5,
            x = (R - r) * Math.cos(a) + dd * Math.cos(((R - r) / r) * a),
            y = (R - r) * Math.sin(a) - dd * Math.sin(((R - r) / r) * a),
            k = 5 * (0.85 + 0.15 * d);
        return { x: 50 + k * x, y: 50 + k * y };
      },
    },
    {
      key: "cardioid", label: "Cardioid",
      point: function (t, d) {
        var a = t * TAU, A = 16 * (0.8 + 0.2 * d), r = A * (1 - Math.cos(a));
        return { x: 50 + Math.cos(a) * r + 15, y: 50 + Math.sin(a) * r };
      },
    },
  ];
  var BY_KEY = {};
  CURVES.forEach(function (c) { BY_KEY[c.key] = c; });

  var N = 48;            // particle count
  var TRAIL = 0.34;      // fraction of the cycle the trail spans
  var DURATION = 5200;   // ms per loop around the curve
  var PULSE = 4400;      // ms per breath pulse
  var ROTATE = 24000;    // ms per full group rotation

  var reduced = (function () {
    try { return matchMedia("(prefers-reduced-motion: reduce)").matches; }
    catch (e) { return false; }
  })();

  // ---- shared ticker: one rAF loop drives every mounted instance ----
  var instances = new Set();
  var raf = 0;
  function tick(now) {
    instances.forEach(function (inst) { inst.frame(now); });
    raf = instances.size ? requestAnimationFrame(tick) : 0;
  }
  function add(inst) { instances.add(inst); if (!raf) raf = requestAnimationFrame(tick); }
  function remove(inst) { instances.delete(inst); if (!instances.size && raf) { cancelAnimationFrame(raf); raf = 0; } }

  function detailAt(now) {
    return 0.52 + ((Math.sin((now / PULSE) * TAU + 0.55) + 1) / 2) * 0.48;
  }

  // Mount a loader into `container`; returns a stop() that cancels + clears it.
  function mount(container, key) {
    var curve = BY_KEY[key] || CURVES[0];
    container.innerHTML = "";
    var svg = document.createElementNS(SVGNS, "svg");
    svg.setAttribute("viewBox", "0 0 100 100");
    svg.setAttribute("fill", "currentColor");
    var g = document.createElementNS(SVGNS, "g");
    svg.appendChild(g);
    container.appendChild(svg);

    var dots = [];
    for (var i = 0; i < N; i++) {
      var c = document.createElementNS(SVGNS, "circle");
      c.setAttribute("r", (1.0 + (1 - i / N) * 1.4).toFixed(2)); // head bigger than tail
      c.setAttribute("opacity", (1 - i / N).toFixed(3));
      g.appendChild(c);
      dots.push(c);
    }

    function paint(head, detail) {
      for (var i = 0; i < N; i++) {
        var t = head - (i / N) * TRAIL;
        t -= Math.floor(t); // wrap into [0,1)
        var p = curve.point(t, detail);
        dots[i].setAttribute("cx", p.x.toFixed(2));
        dots[i].setAttribute("cy", p.y.toFixed(2));
      }
    }

    var inst = {
      frame: function (now) {
        var head = (now % DURATION) / DURATION;
        paint(head, detailAt(now));
        var rot = ((now % ROTATE) / ROTATE) * 360;
        g.setAttribute("transform", "rotate(" + rot.toFixed(2) + " 50 50)");
      },
    };

    if (reduced) {
      paint(0, 1); // single static frame, no animation
    } else {
      add(inst);
    }

    return function stop() {
      remove(inst);
      container.innerHTML = "";
    };
  }

  window.VmuxLoaders = { CURVES: CURVES, mount: mount };
})();
