// Phone menu: the sidebar slides in from the left.
(function () {
  var body = document.body;
  document.querySelectorAll("[data-nav-open]").forEach(function (b) {
    b.addEventListener("click", function () { body.classList.add("nav-open"); });
  });
  document.querySelectorAll("[data-nav-close]").forEach(function (b) {
    b.addEventListener("click", function () { body.classList.remove("nav-open"); });
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") body.classList.remove("nav-open");
  });
})();

// Desktop: collapse the sidebar to icons; remembered in this browser only.
(function () {
  var body = document.body;
  try { if (localStorage.getItem("navCollapsed") === "1") body.classList.add("nav-collapsed"); } catch (e) {}
  document.querySelectorAll("[data-nav-collapse]").forEach(function (b) {
    b.addEventListener("click", function () {
      var on = body.classList.toggle("nav-collapsed");
      b.setAttribute("aria-pressed", on ? "true" : "false");
      try { localStorage.setItem("navCollapsed", on ? "1" : "0"); } catch (e) {}
    });
  });
})();

// Tables with class "sortable": click a heading to sort by that column (numbers as numbers).
(function () {
  function value(cell) {
    var text = (cell.textContent || "").trim();
    var num = parseFloat(text.replace(/[%,]/g, ""));
    return /^-?[\d.,]+%?$/.test(text) && !isNaN(num) ? num : text.toLowerCase();
  }
  document.querySelectorAll("table.sortable").forEach(function (table) {
    var heads = table.querySelectorAll("thead th");
    heads.forEach(function (th, index) {
      th.tabIndex = 0;
      function sort() {
        var asc = th.getAttribute("aria-sort") !== "ascending";
        heads.forEach(function (h) { h.removeAttribute("aria-sort"); });
        th.setAttribute("aria-sort", asc ? "ascending" : "descending");
        var body = table.tBodies[0];
        var rows = Array.prototype.slice.call(body.rows);
        rows.sort(function (a, b) {
          var x = value(a.cells[index] || a), y = value(b.cells[index] || b);
          if (typeof x === typeof y) return (x > y ? 1 : x < y ? -1 : 0) * (asc ? 1 : -1);
          return (typeof x === "number" ? -1 : 1) * (asc ? 1 : -1);
        });
        rows.forEach(function (r) { body.appendChild(r); });
      }
      th.addEventListener("click", sort);
      th.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); sort(); } });
    });
  });
})();
