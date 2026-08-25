(function () {
  "use strict";

  var collator = new Intl.Collator("ro", {
    numeric: true,
    sensitivity: "base"
  });

  function rawValue(cell) {
    return (cell.getAttribute("data-sort-value") || cell.textContent || "")
      .replace(/\u00a0/g, " ")
      .trim();
  }

  function numberValue(text) {
    var match = text.replace(/\s/g, "").match(/[-+]?\d[\d.,]*/);
    if (!match) return null;
    var token = match[0];
    if (token.indexOf(",") !== -1) {
      token = token.replace(/\./g, "").replace(",", ".");
    } else {
      var parts = token.split(".");
      if (parts.length > 2 ||
          (parts.length === 2 && parts[1].length === 3)) {
        token = parts.join("");
      }
    }
    var value = Number(token);
    return Number.isFinite(value) ? value : null;
  }

  function dateValue(text) {
    var ro = text.match(/(\d{2})\.(\d{2})\.(\d{4})/);
    if (ro) return Date.UTC(Number(ro[3]), Number(ro[2]) - 1, Number(ro[1]));
    var iso = text.match(/(\d{4})-(\d{2})-(\d{2})/);
    if (iso) return Date.UTC(Number(iso[1]), Number(iso[2]) - 1, Number(iso[3]));
    return null;
  }

  function normalized(cell, type) {
    var text = rawValue(cell);
    if (!text || text === "—" || text === "-") return { kind: "missing", value: null };

    var date = type === "date" || type === "auto" ? dateValue(text) : null;
    if (date !== null) return { kind: "date", value: date };

    var number = type === "number" || type === "auto" ? numberValue(text) : null;
    if (number !== null && (type === "number" || /^[-+]?\s*\d/.test(text))) {
      return { kind: "number", value: number };
    }
    return { kind: "text", value: text };
  }

  function compareValues(a, b) {
    if (a.kind === "missing") return b.kind === "missing" ? 0 : 1;
    if (b.kind === "missing") return -1;
    if (a.kind === b.kind) {
      return a.kind === "text"
        ? collator.compare(a.value, b.value)
        : a.value - b.value;
    }
    var rank = { date: 0, number: 1, text: 2 };
    return rank[a.kind] - rank[b.kind];
  }

  function enhance(table) {
    var body = table.tBodies[0];
    var headerRow = table.tHead && table.tHead.rows[table.tHead.rows.length - 1];
    if (!body || !headerRow) return;

    Array.prototype.forEach.call(body.rows, function (row, index) {
      row.setAttribute("data-original-order", String(index));
    });

    Array.prototype.forEach.call(headerRow.cells, function (header, column) {
      var label = header.textContent.trim();
      var button = document.createElement("button");
      button.type = "button";
      button.className = "sort-button";
      button.setAttribute("aria-label", "Sortează după " + label + ", crescător");
      button.title = "Sortează după " + label;
      while (header.firstChild) button.appendChild(header.firstChild);
      header.appendChild(button);
      header.setAttribute("aria-sort", "none");

      button.addEventListener("click", function () {
        var direction = header.getAttribute("aria-sort") === "ascending"
          ? "descending"
          : "ascending";
        Array.prototype.forEach.call(headerRow.cells, function (other) {
          other.setAttribute("aria-sort", "none");
          var otherButton = other.querySelector(".sort-button");
          if (otherButton) {
            var otherLabel = otherButton.textContent.trim();
            otherButton.setAttribute(
              "aria-label",
              "Sortează după " + otherLabel + ", crescător"
            );
          }
        });
        header.setAttribute("aria-sort", direction);
        button.setAttribute(
          "aria-label",
          "Sortat după " + label + ", " +
          (direction === "ascending" ? "crescător" : "descrescător") +
          "; activează pentru a inversa ordinea"
        );

        var type = header.getAttribute("data-sort-type") || "auto";
        var rows = Array.prototype.slice.call(body.rows);
        rows.sort(function (left, right) {
          var leftValue = normalized(left.cells[column], type);
          var rightValue = normalized(right.cells[column], type);
          var result = compareValues(leftValue, rightValue);
          if (result === 0) {
            return Number(left.getAttribute("data-original-order")) -
              Number(right.getAttribute("data-original-order"));
          }
          if (leftValue.kind === "missing" || rightValue.kind === "missing") {
            return result;
          }
          return direction === "ascending" ? result : -result;
        });
        rows.forEach(function (row) { body.appendChild(row); });
      });
    });
  }

  Array.prototype.forEach.call(
    document.querySelectorAll("table.sortable"),
    enhance
  );
})();
