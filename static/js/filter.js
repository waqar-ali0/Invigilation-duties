// Instant filters. Two kinds:
//   <input data-filter="#scope" data-filter-items=".card">  hides items inside #scope that don't contain the text
//   <input data-filter-select="#select-id">                  narrows the options of a <select>
(function () {
  function norm(s) { return (s || "").toLowerCase().replace(/\s+/g, " ").trim(); }

  document.querySelectorAll("input[data-filter]").forEach(function (input) {
    var scope = document.querySelector(input.dataset.filter);
    if (!scope) return;
    var items = scope.querySelectorAll(input.dataset.filterItems || "[data-filter-item]");
    var empty = scope.querySelector("[data-filter-empty]");
    input.addEventListener("input", function () {
      var q = norm(input.value), shown = 0;
      items.forEach(function (el) {
        var match = !q || norm(el.textContent).indexOf(q) !== -1;
        el.hidden = !match;
        if (match) shown++;
      });
      if (empty) empty.hidden = shown !== 0;
    });
  });

  document.querySelectorAll("input[data-filter-select]").forEach(function (input) {
    var select = document.querySelector(input.dataset.filterSelect);
    if (!select) return;
    var all = Array.prototype.slice.call(select.options);
    input.addEventListener("input", function () {
      var q = norm(input.value), current = select.value;
      select.innerHTML = "";
      all.forEach(function (opt) {
        if (!q || norm(opt.textContent).indexOf(q) !== -1) select.appendChild(opt);
      });
      if (!select.options.length) {
        var none = new Option("No matches", "");
        none.disabled = true;
        select.appendChild(none);
      }
      select.value = Array.prototype.some.call(select.options, function (o) { return o.value === current; }) ? current : (select.options[0] || {}).value;
    });
  });
})();
