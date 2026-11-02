"use strict";

/*
 * EpiScope dashboard frontend.
 * Uses aggregated API data only. Never request patient-level records.
 */

const API = "/api";
const $ = (id) => document.getElementById(id);
const numberFormat = new Intl.NumberFormat("en-US");

let mapFeatures = [];
let mapSvg = null;
let mapGeoJSON = null;

const COLORS = [
  "#007c70",
  "#d58a27",
  "#6b65a8",
  "#bd4855",
  "#2878b5",
  "#8a6638",
  "#329b78",
  "#a44c86",
  "#718b31",
  "#526579",
];

const state = {
  district: "ALL",
  disease: "ALL",
  from: "",
  to: "",
  districts: [],
  diseases: [],
  districtNames: {},
  mapData: null,
  mapSvg: null,
  refreshId: 0,
};

const tooltip = d3.select("#tooltip");

function showTooltip(event, html) {
  if (tooltip.empty()) {
    console.error("Tooltip element #tooltip not found.");
    return;
  }

  tooltip.html(html).style("display", "block");

  positionTooltip(event);
}

function positionTooltip(event) {
  if (tooltip.empty()) return;

  const tooltipNode = tooltip.node();
  const rect = tooltipNode.getBoundingClientRect();

  const left = Math.max(
    8,
    Math.min(event.clientX + 12, window.innerWidth - rect.width - 8),
  );

  const top = Math.max(
    8,
    Math.min(event.clientY + 12, window.innerHeight - rect.height - 8),
  );

  tooltip.style("left", `${left}px`).style("top", `${top}px`);
}

function hideTooltip() {
  if (tooltip.empty()) return;

  tooltip.style("display", "none").html("");
}

/* -------------------- GENERAL HELPERS -------------------- */

function formatNumber(value) {
  if (value === "Suppressed" || value == null) {
    return value ?? "—";
  }

  const n = Number(value);
  return Number.isFinite(n) ? numberFormat.format(n) : "—";
}

function escapeHTML(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (char) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      })[char],
  );
}

async function getJSON(path) {
  const response = await fetch(`${API}${path}`);

  if (!response.ok) {
    let message = `Request failed (${response.status})`;

    try {
      const error = await response.json();
      message = error.detail || message;
    } catch (_) {}

    throw new Error(message);
  }

  const data = await response.json();

  // Support both array responses and wrapped API responses.
  if (Array.isArray(data)) return data;

  if (Array.isArray(data.data)) return data.data;
  if (Array.isArray(data.rows)) return data.rows;
  if (Array.isArray(data.results)) return data.results;
  if (Array.isArray(data.forecasts)) return data.forecasts;

  return data;
}

function setText(id, value) {
  const node = $(id);
  if (node) {
    node.textContent = value == null ? "—" : String(value);
  }
}

function setStatus(message, status = "unknown") {
  setText("apiStatus", message);

  const dot = document.querySelector(".status-dot");

  if (dot) {
    dot.classList.toggle("online", status === "online");
    dot.classList.toggle("offline", status === "offline");
  }
}

function showError(id, message) {
  const node = $(id);
  if (!node) return;

  node.replaceChildren();

  const div = document.createElement("div");
  div.className = "error";
  div.textContent = message;

  node.appendChild(div);
}

function showEmpty(id, message) {
  const node = $(id);
  if (!node) return;

  node.replaceChildren();

  const div = document.createElement("div");
  div.className = "empty-state";
  div.textContent = message;

  node.appendChild(div);
}

function positionTooltip(event) {
  if (tooltip.empty()) return;

  const left = Math.max(
    8,
    Math.min(event.clientX + 12, window.innerWidth - 290),
  );

  const top = Math.max(
    8,
    Math.min(event.clientY + 12, window.innerHeight - 120),
  );

  tooltip.style("left", `${left}px`).style("top", `${top}px`);
}

function hideTooltip() {
  tooltip.style("display", "none");
}

function currentParams(extra = {}) {
  const params = new URLSearchParams({
    district: state.district,
    disease: state.disease,
  });

  if (state.from) params.set("date_from", state.from);
  if (state.to) params.set("date_to", state.to);

  Object.entries(extra).forEach(([key, value]) => {
    if (value !== undefined && value !== null) {
      params.set(key, String(value));
    }
  });

  return params.toString();
}

function buildParams(options = {}) {
  const params = new URLSearchParams();

  params.set("district", options.district ?? state.district);

  params.set("disease", options.disease ?? state.disease);

  const from = options.from ?? state.from;
  const to = options.to ?? state.to;

  if (from) params.set("date_from", from);
  if (to) params.set("date_to", to);

  return params;
}

function setSelectOptions(selectId, rows, valueKey, labelKey, firstLabel) {
  const select = $(selectId);
  if (!select) return;

  const previous = select.value;

  select.replaceChildren();

  const all = document.createElement("option");
  all.value = "ALL";
  all.textContent = firstLabel;
  select.appendChild(all);

  rows.forEach((row) => {
    const option = document.createElement("option");

    option.value = String(row[valueKey]);
    option.textContent = String(row[labelKey]);

    select.appendChild(option);
  });

  if ([...select.options].some((option) => option.value === previous)) {
    select.value = previous;
  }
}

function renderTable(id, rows, columns, emptyMessage, colspan) {
  const body = $(id);
  if (!body) return;

  body.replaceChildren();

  if (!Array.isArray(rows) || !rows.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");

    td.colSpan = colspan || columns.length;
    td.textContent = emptyMessage;

    tr.appendChild(td);
    body.appendChild(tr);

    return;
  }

  rows.forEach((row) => {
    const tr = document.createElement("tr");

    columns.forEach((column) => {
      const td = document.createElement("td");
      const value = column.value(row);

      if (column.className) {
        td.className = column.className;
      }

      td.textContent = value == null ? "—" : String(value);
      tr.appendChild(td);
    });

    body.appendChild(tr);
  });
}

function renderMetricChips(id, metrics) {
  const container = $(id);
  if (!container) return;

  container.replaceChildren();

  metrics.forEach(([label, value]) => {
    const chip = document.createElement("div");
    chip.className = "metric-chip";

    const name = document.createElement("span");
    name.textContent = label;

    const strong = document.createElement("strong");
    strong.textContent = String(value ?? "—");

    chip.append(name, strong);
    container.appendChild(chip);
  });
}

function parseDate(value) {
  if (!value) return null;

  const date = new Date(`${String(value).slice(0, 10)}T00:00:00`);

  return Number.isNaN(+date) ? null : date;
}

function chartDimensions(containerId, defaultHeight = 280) {
  const node = $(containerId);

  return {
    width: Math.max(node?.clientWidth || 0, 280),
    height: defaultHeight,
  };
}

function addAxes(svg, x, y, width, height, margin, dateAxis = true) {
  svg
    .append("g")
    .attr("class", "grid")
    .attr("transform", `translate(${margin.left},0)`)
    .call(
      d3
        .axisLeft(y)
        .ticks(5)
        .tickSize(-(width - margin.left - margin.right))
        .tickFormat(""),
    );

  svg
    .append("g")
    .attr("class", "axis")
    .attr("transform", `translate(0,${height - margin.bottom})`)
    .call(
      dateAxis
        ? d3.axisBottom(x).ticks(5).tickFormat(d3.timeFormat("%Y-%m"))
        : d3.axisBottom(x),
    );

  svg
    .append("g")
    .attr("class", "axis")
    .attr("transform", `translate(${margin.left},0)`)
    .call(d3.axisLeft(y).ticks(5));
}

/*
 * Apply filters defensively when the API response includes identifiers.
 * The API must still perform the authoritative filtering server-side.
 */
function filterRows(rows, options = {}) {
  let result = Array.isArray(rows) ? rows : [];

  const district = options.district ?? state.district;
  const disease = options.disease ?? state.disease;
  const from = options.from ?? state.from;
  const to = options.to ?? state.to;

  if (district !== "ALL") {
    const hasDistrict = result.some((row) => row.district_id != null);

    if (hasDistrict) {
      result = result.filter(
        (row) => String(row.district_id) === String(district),
      );
    }
  }

  if (disease !== "ALL") {
    const hasDisease = result.some((row) => row.disease != null);

    if (hasDisease) {
      result = result.filter(
        (row) =>
          String(row.disease).toLowerCase() === String(disease).toLowerCase(),
      );
    }
  }

  if (from || to) {
    result = result.filter((row) => {
      const dateValue =
        row.week_start ?? row.forecast_week ?? row.date ?? row.week_of_year;

      if (dateValue == null) return true;

      const date = String(dateValue).slice(0, 10);

      if (from && dateValue.length >= 8 && date < from) return false;
      if (to && dateValue.length >= 8 && date > to) return false;

      return true;
    });
  }

  return result;
}

/* -------------------- FILTERS -------------------- */

async function loadFilters() {
  const [districtRows, summary] = await Promise.all([
    getJSON("/districts"),
    getJSON("/summary"),
  ]);

  state.districts = Array.isArray(districtRows) ? districtRows : [];

  state.districtNames = {};

  state.districts.forEach((row) => {
    state.districtNames[String(row.district_id)] =
      row.district_name || row.district_id;
  });

  const sortedDistricts = [...state.districts].sort((a, b) =>
    String(a.district_name || a.district_id).localeCompare(
      String(b.district_name || b.district_id),
    ),
  );

  setSelectOptions(
    "districtFilter",
    sortedDistricts,
    "district_id",
    "district_name",
    "All districts",
  );

  state.diseases = (summary.disease_totals || [])
    .map((row) => String(row.disease))
    .filter(Boolean)
    .sort((a, b) => a.localeCompare(b));

  setSelectOptions(
    "diseaseFilter",
    state.diseases.map((disease) => ({ disease })),
    "disease",
    "disease",
    "All diseases",
  );
}

function readFilters() {
  state.district = $("districtFilter")?.value || "ALL";
  state.disease = $("diseaseFilter")?.value || "ALL";
  state.from = $("dateFrom")?.value || "";
  state.to = $("dateTo")?.value || "";
}

/* -------------------- SUMMARY -------------------- */

async function loadSummary() {
  const data = await getJSON(`/summary?${currentParams()}`);

  setText("totalCases", formatNumber(data.total_cases));
  setText("districtCount", formatNumber(data.districts));
  setText("alertCount", formatNumber(data.alert_weeks));
  setText("bedCount", formatNumber(data.estimated_beds));

  setText(
    "lastUpdated",
    `Available data range: ${data.date_min || "—"} to ${data.date_max || "—"}`,
  );
}

/* -------------------- OBSERVED + FORECAST TREND -------------------- */

function renderObservedTrend(rows, forecastRows = []) {
  const id = "trendChart";
  const container = d3.select(`#${id}`);

  container.selectAll("*").remove();

  const observed = (rows || [])
    .map((row) => ({
      date: parseDate(row.week_start),
      cases: Number(row.cases),
    }))
    .filter((d) => d.date && Number.isFinite(d.cases))
    .sort((a, b) => a.date - b.date);

  const forecasts = (forecastRows || [])
    .map((row) => ({
      date: parseDate(row.forecast_week ?? row.week_start),
      forecast: Number(row.forecast_cases ?? row.forecast),
      lower: Number(row.lower_80 ?? row.lower_bound),
      upper: Number(row.upper_80 ?? row.upper_bound),
    }))
    .filter((d) => d.date && Number.isFinite(d.forecast))
    .sort((a, b) => a.date - b.date);

  if (!observed.length && !forecasts.length) {
    showEmpty(id, "No observed or forecast data available for these filters.");
    setText("forecastMessage", "No forecast data available.");
    return;
  }

  const width = chartDimensions(id, 290).width;
  const height = 290;

  const margin = {
    top: 15,
    right: 20,
    bottom: 42,
    left: 48,
  };

  const allDates = [
    ...observed.map((d) => d.date),
    ...forecasts.map((d) => d.date),
  ];

  let dateExtent = d3.extent(allDates);

  if (+dateExtent[0] === +dateExtent[1]) {
    dateExtent = [
      new Date(+dateExtent[0] - 86400000 * 7),
      new Date(+dateExtent[1] + 86400000 * 7),
    ];
  }

  const allValues = [
    ...observed.map((d) => d.cases),
    ...forecasts.map((d) => d.forecast),
    ...forecasts.flatMap((d) => [d.lower, d.upper]).filter(Number.isFinite),
  ];

  const maxValue = d3.max(allValues) || 1;

  const x = d3
    .scaleTime()
    .domain(dateExtent)
    .range([margin.left, width - margin.right]);

  const y = d3
    .scaleLinear()
    .domain([0, maxValue * 1.15])
    .nice()
    .range([height - margin.bottom, margin.top]);

  const svg = container
    .append("svg")
    .attr("viewBox", `0 0 ${width} ${height}`)
    .attr("role", "img")
    .attr("aria-label", "Observed weekly cases and forecasts");

  addAxes(svg, x, y, width, height, margin);

  if (observed.length) {
    svg
      .append("path")
      .datum(observed)
      .attr("fill", "none")
      .attr("stroke", "#007c70")
      .attr("stroke-width", 2.5)
      .attr(
        "d",
        d3
          .line()
          .defined((d) => Number.isFinite(d.cases))
          .x((d) => x(d.date))
          .y((d) => y(d.cases))
          .curve(d3.curveMonotoneX),
      );

    svg
      .selectAll(".observed-point")
      .data(observed)
      .join("circle")
      .attr("class", "observed-point")
      .attr("cx", (d) => x(d.date))
      .attr("cy", (d) => y(d.cases))
      .attr("r", 2.8)
      .attr("fill", "#007c70")
      .on("mousemove", (event, d) =>
        showTooltip(
          event,
          `<strong>${d3.timeFormat("%Y-%m-%d")(d.date)}</strong><br>` +
            `Observed: ${formatNumber(d.cases)}`,
        ),
      )
      .on("mouseleave", hideTooltip);
  }

  if (forecasts.length) {
    const intervalData = forecasts.filter(
      (d) => Number.isFinite(d.lower) && Number.isFinite(d.upper),
    );

    if (intervalData.length) {
      svg
        .append("path")
        .datum(intervalData)
        .attr("fill", "#d9e8f5")
        .attr("opacity", 0.75)
        .attr(
          "d",
          d3
            .area()
            .x((d) => x(d.date))
            .y0((d) => y(d.lower))
            .y1((d) => y(d.upper))
            .curve(d3.curveMonotoneX),
        );
    }

    svg
      .append("path")
      .datum(forecasts)
      .attr("fill", "none")
      .attr("stroke", "#2878b5")
      .attr("stroke-width", 2.5)
      .attr("stroke-dasharray", "6 4")
      .attr(
        "d",
        d3
          .line()
          .x((d) => x(d.date))
          .y((d) => y(d.forecast))
          .curve(d3.curveMonotoneX),
      );

    svg
      .selectAll(".forecast-point")
      .data(forecasts)
      .join("circle")
      .attr("class", "forecast-point")
      .attr("cx", (d) => x(d.date))
      .attr("cy", (d) => y(d.forecast))
      .attr("r", 3)
      .attr("fill", "#2878b5")
      .on("mousemove", (event, d) =>
        showTooltip(
          event,
          `<strong>${d3.timeFormat("%Y-%m-%d")(d.date)}</strong><br>` +
            `Forecast: ${formatNumber(d.forecast)}` +
            `${Number.isFinite(d.lower) ? `<br>Lower: ${formatNumber(d.lower)}` : ""}` +
            `${Number.isFinite(d.upper) ? `<br>Upper: ${formatNumber(d.upper)}` : ""}`,
        ),
      )
      .on("mouseleave", hideTooltip);
  }

  setText(
    "forecastMessage",
    forecasts.length
      ? `${forecasts.length} forecast periods loaded. ` +
          "Uncertainty bands appear only when supplied by the API."
      : "Forecast data is unavailable. Observed weekly values are displayed.",
  );
}

async function loadTrend() {
  const params = currentParams();

  const [weeklyRows, forecastRows] = await Promise.all([
    getJSON(`/weekly?${params}`),
    getJSON(`/forecasts?${params}`).catch((error) => {
      console.warn("Forecast endpoint unavailable:", error.message);
      return [];
    }),
  ]);

  const filteredWeekly = filterRows(weeklyRows);
  const filteredForecast = filterRows(forecastRows);

  renderObservedTrend(filteredWeekly, filteredForecast);
}

/* -------------------- DISEASE CHART -------------------- */

function renderDiseaseChart(rows) {
  const id = "diseaseChart";
  const container = d3.select(`#${id}`);

  container.selectAll("*").remove();

  const data = (rows || [])
    .map((row) => ({
      disease: String(row.disease || "Unknown"),
      cases: Number(row.cases),
    }))
    .filter((d) => Number.isFinite(d.cases))
    .sort((a, b) => a.cases - b.cases);

  if (!data.length) {
    showEmpty(id, "No disease totals available.");
    return;
  }

  const width = chartDimensions(id, 280).width;
  const margin = {
    top: 12,
    right: 48,
    bottom: 30,
    left: 105,
  };

  const height = Math.max(230, data.length * 38 + 45);

  const svg = container
    .append("svg")
    .attr("viewBox", `0 0 ${width} ${height}`)
    .attr("role", "img")
    .attr("aria-label", "Reported cases by disease");

  const x = d3
    .scaleLinear()
    .domain([0, d3.max(data, (d) => d.cases) || 1])
    .nice()
    .range([margin.left, width - margin.right]);

  const y = d3
    .scaleBand()
    .domain(data.map((d) => d.disease))
    .range([margin.top, height - margin.bottom])
    .padding(0.25);

  svg
    .append("g")
    .attr("class", "axis")
    .attr("transform", `translate(0,${height - margin.bottom})`)
    .call(d3.axisBottom(x).ticks(4));

  svg
    .append("g")
    .attr("class", "axis")
    .attr("transform", `translate(${margin.left},0)`)
    .call(d3.axisLeft(y).tickSize(0));

  svg
    .selectAll("rect")
    .data(data)
    .join("rect")
    .attr("x", margin.left)
    .attr("y", (d) => y(d.disease))
    .attr("width", (d) => Math.max(0, x(d.cases) - margin.left))
    .attr("height", y.bandwidth())
    .attr("rx", 4)
    .attr("fill", "#2878b5")
    .on("mousemove", (event, d) =>
      showTooltip(
        event,
        `<strong>${escapeHTML(d.disease)}</strong><br>` +
          `Cases: ${formatNumber(d.cases)}`,
      ),
    )
    .on("mouseleave", hideTooltip);

  svg
    .selectAll(".bar-label")
    .data(data)
    .join("text")
    .attr("class", "bar-label")
    .attr("x", (d) => x(d.cases) + 5)
    .attr("y", (d) => y(d.disease) + y.bandwidth() / 2 + 4)
    .text((d) => formatNumber(d.cases));
}

async function loadDiseaseChart() {
  const rows = await getJSON(
    `/disease-counts?${currentParams({ district: state.district })}`,
  );

  const filtered = filterRows(rows);

  renderDiseaseChart(filtered);
}

/* -------------------- MULTI-DISEASE TREND -------------------- */

async function loadMultiDiseaseTrend() {
  const legend = $("mapTrendLegend");
  const container = d3.select("#mapTrendChart");

  if (!container.node()) {
    console.error("Map trend chart container not found.");
    return;
  }

  container.selectAll("*").remove();

  if (legend) legend.replaceChildren();

  // Capture the current filter selection.
  const selectedDistrict = state.district;
  const selectedDisease = state.disease;
  const refreshVersion = state.refreshId;

  // Show all diseases or only the selected disease.
  const diseases =
    selectedDisease === "ALL" ? [...state.diseases] : [selectedDisease];

  if (!diseases.length) {
    showEmpty("mapTrendChart", "No disease list available.");
    return;
  }

  try {
    // Fetch weekly cases separately for each disease.
    const results = await Promise.all(
      diseases.map(async (disease) => {
        const params = buildParams({
          district: selectedDistrict,
          disease,
        });

        const rows = await getJSON(`/weekly?${params.toString()}`);

        const filtered = filterRows(rows, {
          district: selectedDistrict,
          disease,
        });

        return {
          disease,
          values: filtered
            .map((row) => ({
              date: parseDate(row.week_start),
              cases: row.cases === "Suppressed" ? null : Number(row.cases),
            }))
            .filter(
              (d) => d.date && (d.cases === null || Number.isFinite(d.cases)),
            )
            .sort((a, b) => a.date - b.date),
        };
      }),
    );

    // Ignore outdated responses if the user changed districts
    // while the requests were still running.
    if (refreshVersion !== state.refreshId) return;

    const series = results.filter((s) => s.values.length);

    if (!series.length) {
      showEmpty(
        "mapTrendChart",
        "No weekly disease series found for the selected district and dates.",
      );
      return;
    }

    // Give each disease its own line colour.
    const color = d3
      .scaleOrdinal()
      .domain(series.map((s) => s.disease))
      .range(COLORS);

    // Build the disease legend.
    series.forEach((s) => {
      const item = document.createElement("span");
      item.className = "trend-legend-item";

      const swatch = document.createElement("span");
      swatch.className = "trend-legend-swatch";
      swatch.style.backgroundColor = color(s.disease);

      const label = document.createElement("span");
      label.textContent = s.disease;

      item.append(swatch, label);
      legend?.appendChild(item);
    });

    const all = series.flatMap((s) =>
      s.values.filter((d) => Number.isFinite(d.cases)),
    );

    const width = chartDimensions("mapTrendChart", 250).width;
    const height = 250;

    const margin = {
      top: 20,
      right: 20,
      bottom: 42,
      left: 48,
    };

    let extent = d3.extent(all, (d) => d.date);

    if (+extent[0] === +extent[1]) {
      extent = [
        new Date(+extent[0] - 86400000 * 7),
        new Date(+extent[1] + 86400000 * 7),
      ];
    }

    const x = d3
      .scaleTime()
      .domain(extent)
      .range([margin.left, width - margin.right]);

    const y = d3
      .scaleLinear()
      .domain([0, (d3.max(all, (d) => d.cases) || 1) * 1.1])
      .nice()
      .range([height - margin.bottom, margin.top]);

    const svg = container
      .append("svg")
      .attr("viewBox", `0 0 ${width} ${height}`)
      .attr("width", "100%")
      .attr("role", "img")
      .attr("aria-label", "Weekly case trends by disease");

    addAxes(svg, x, y, width, height, margin);

    // Draw one line for each disease.
    series.forEach((s, index) => {
      const diseaseColor = color(s.disease);

      svg
        .append("path")
        .datum(s.values)
        .attr("fill", "none")
        .attr("stroke", diseaseColor)
        .attr("stroke-width", 2.5)
        .attr("stroke-linejoin", "round")
        .attr("stroke-linecap", "round")
        .attr(
          "d",
          d3
            .line()
            .x((d) => x(d.date))
            .y((d) => y(d.cases))
            .curve(d3.curveMonotoneX),
        );

      // Add points to each disease line.
      svg
        .selectAll(`.disease-series-${index}`)
        .data(s.values.filter((d) => Number.isFinite(d.cases)))
        .join("circle")
        .attr("class", `disease-series-${index}`)
        .attr("cx", (d) => x(d.date))
        .attr("cy", (d) => y(d.cases))
        .attr("r", 3)
        .attr("fill", diseaseColor)
        .attr("stroke", "#ffffff")
        .attr("stroke-width", 1)
        .style("cursor", "pointer")
        .on("mousemove", (event, d) =>
          showTooltip(
            event,
            `<strong>${escapeHTML(s.disease)}</strong><br>` +
              `${d3.timeFormat("%Y-%m-%d")(d.date)}<br>` +
              `Cases: ${formatNumber(d.cases)}`,
          ),
        )
        .on("mouseleave", hideTooltip);
    });

    // Display which district is currently selected.
    const districtLabel =
      selectedDistrict === "ALL"
        ? "All districts"
        : state.districtNames[selectedDistrict] || selectedDistrict;

    setText("mapTrendMessage", `Weekly disease trends — ${districtLabel}`);
  } catch (error) {
    console.error("Could not load multi-disease trends:", error);

    if (refreshVersion === state.refreshId) {
      showEmpty("mapTrendChart", "Unable to load weekly disease trends.");
    }
  }
}

/* -------------------- DISTRICT TABLE -------------------- */

async function loadDistrictDiseaseCounts() {
  const rows = await getJSON(
    `/disease-counts?${currentParams({ district: state.district })}`,
  );

  const filtered = filterRows(rows);

  const districtName =
    state.district === "ALL"
      ? "All districts"
      : state.districtNames[state.district] || state.district;

  setText("selectedDistrictTitle", districtName);

  renderTable(
    "districtDiseaseTable",
    filtered,
    [
      { value: (r) => r.disease || "Unknown" },
      { value: (r) => formatNumber(r.cases) },
      { value: (r) => (r.suppressed ? "Suppressed" : "Aggregated") },
    ],
    "No disease counts available.",
    3,
  );
}

/* -------------------- MAP -------------------- */

/* -------------------- MAP -------------------- */

function getFeatureDistrictId(feature) {
  const p = feature?.properties || {};

  return String(
    p.district_id ?? p.ADM2_PCODE ?? p.DISTRICT_ID ?? p.shapeID ?? "",
  );
}

function getFeatureName(feature) {
  const p = feature?.properties || {};

  return (
    p.district_name ||
    p.shapeName ||
    p.ADM2_EN ||
    p.NAME_2 ||
    p.name ||
    "District"
  );
}

function getFeatureCases(feature) {
  const p = feature?.properties || {};

  const raw = p.total_cases ?? p.cases ?? p.reported_cases;

  if (raw == null || raw === "" || p.suppressed === true) {
    return null;
  }

  const value = Number(raw);

  return Number.isFinite(value) && value >= 0 ? value : null;
}

function renderMap(geojson) {
  const container = d3.select("#districtMap");

  if (!container.node()) {
    console.error("Map container #districtMap not found.");
    return;
  }

  container.selectAll("*").remove();
  mapFeatures = [];
  mapSvg = null;
  mapGeoJSON = null;

  if (
    !geojson ||
    geojson.type !== "FeatureCollection" ||
    !Array.isArray(geojson.features)
  ) {
    showEmpty("districtMap", "Invalid GeoJSON returned by /api/map.");
    console.error("Invalid GeoJSON:", geojson);
    return;
  }

  mapFeatures = geojson.features.filter(
    (feature) =>
      feature?.geometry &&
      ["Polygon", "MultiPolygon"].includes(feature.geometry.type),
  );

  if (!mapFeatures.length) {
    showEmpty("districtMap", "No valid district polygons found.");
    return;
  }

  mapGeoJSON = JSON.parse(
    JSON.stringify({
      type: "FeatureCollection",
      features: mapFeatures,
    }),
  );

  // Correct polygon winding for D3 spherical rendering.
  mapGeoJSON.features.forEach((feature) => {
    if (d3.geoArea(feature) > 2 * Math.PI) {
      const geometry = feature.geometry;

      function reverseRings(polygonCoordinates) {
        polygonCoordinates.forEach((ring) => ring.reverse());
      }

      if (geometry.type === "Polygon") {
        reverseRings(geometry.coordinates);
      } else if (geometry.type === "MultiPolygon") {
        geometry.coordinates.forEach(reverseRings);
      }
    }
  });

  mapFeatures = mapGeoJSON.features;

  const width = Math.max(container.node().clientWidth || 0, 320);
  const height = Math.max(400, Math.min(720, width * 0.95));

  const svg = container
    .append("svg")
    .attr("viewBox", `0 0 ${width} ${height}`)
    .attr("width", "100%")
    .attr("height", height)
    .attr("preserveAspectRatio", "xMidYMid meet")
    .attr("role", "img")
    .attr("aria-label", "Bangladesh district case map");

  const projection = d3.geoMercator().fitExtent(
    [
      [15, 15],
      [width - 15, height - 15],
    ],
    mapGeoJSON,
  );

  const path = d3.geoPath().projection(projection);

  // Blue colour scale.
  const caseValues = mapFeatures
    .map(getFeatureCases)
    .filter((value) => value !== null);

  const maxCases = d3.max(caseValues) || 1;

  const blueColors = ["#f7fbff", "#9ecae1", "#4292c6", "#084594"];

  const caseColor = d3
    .scaleSequential()
    .domain([0, maxCases])
    .interpolator(d3.interpolateRgbBasis(blueColors))
    .clamp(true);

  mapSvg = svg;
  state.mapSvg = svg;

  // Draw districts.
  svg
    .selectAll("path.district")
    .data(mapFeatures)
    .join("path")
    .attr("class", "district")
    .attr("d", path)
    .attr("fill", (feature) => {
      const cases = getFeatureCases(feature);
      return cases === null ? "#b8c0c8" : caseColor(cases);
    })
    .attr("stroke", "#ffffff")
    .attr("stroke-width", 1)
    .attr("stroke-linejoin", "round")
    .style("cursor", "pointer")

    // Display information as soon as the pointer enters a district.
    .on("mouseenter", (event, feature) => {
      const cases = getFeatureCases(feature);
      const name = getFeatureName(feature) || "Unknown district";
      const id = String(getFeatureDistrictId(feature) || "Unmapped");

      const html =
        `<strong>${escapeHTML(name)}</strong><br>` +
        `District ID: ${escapeHTML(id)}<br>` +
        `Cases: ${
          cases === null ? "Suppressed or unavailable" : formatNumber(cases)
        }`;

      tooltip.attr("data-district-id", id);
      showTooltip(event, html);
    })

    // Keep the tooltip beside the pointer.
    .on("mousemove", (event) => {
      positionTooltip(event);
    })

    // Hide the tooltip when the pointer leaves the district.
    .on("mouseleave", () => {
      hideTooltip();
      tooltip.attr("data-district-id", null);
    })

    // Clicking is only for selecting a district.
    .on("click", (_, feature) => {
      selectMapDistrict(feature);
    });

  // District labels.
  svg
    .selectAll("text.district-label")
    .data(mapFeatures)
    .join("text")
    .attr("class", "district-label")
    .attr("transform", (feature) => {
      const center = path.centroid(feature);

      if (!center.every(Number.isFinite)) {
        return "translate(-100,-100)";
      }

      return `translate(${center[0]},${center[1]})`;
    })
    .attr("text-anchor", "middle")
    .attr("dominant-baseline", "central")
    .style("pointer-events", "none")
    .text((feature) => {
      const id = getFeatureDistrictId(feature);

      return getFeatureName(feature) || state.districtNames[id] || "";
    });

  // Blue gradient legend.
  const legendWidth = Math.min(220, width - 50);
  const legendHeight = 10;
  const legendX = 25;
  const legendY = height - 45;

  const defs = svg.append("defs");

  const gradient = defs
    .append("linearGradient")
    .attr("id", "district-case-gradient")
    .attr("x1", "0%")
    .attr("x2", "100%")
    .attr("y1", "0%")
    .attr("y2", "0%");

  [
    { offset: "0%", color: "#f7fbff" },
    { offset: "33.33%", color: "#9ecae1" },
    { offset: "66.66%", color: "#4292c6" },
    { offset: "100%", color: "#084594" },
  ].forEach((stop) => {
    gradient
      .append("stop")
      .attr("offset", stop.offset)
      .attr("stop-color", stop.color);
  });

  const legend = svg.append("g").attr("class", "map-legend");

  legend
    .append("rect")
    .attr("x", legendX)
    .attr("y", legendY)
    .attr("width", legendWidth)
    .attr("height", legendHeight)
    .attr("rx", 3)
    .attr("fill", "url(#district-case-gradient)");

  legend
    .append("text")
    .attr("x", legendX)
    .attr("y", legendY - 7)
    .attr("font-size", 10)
    .attr("fill", "#526579")
    .text("Fewer cases");

  legend
    .append("text")
    .attr("x", legendX + legendWidth)
    .attr("y", legendY - 7)
    .attr("text-anchor", "end")
    .attr("font-size", 10)
    .attr("fill", "#526579")
    .text("More cases");

  legend
    .append("text")
    .attr("x", legendX)
    .attr("y", legendY + 23)
    .attr("font-size", 10)
    .attr("fill", "#526579")
    .text("0");

  legend
    .append("text")
    .attr("x", legendX + legendWidth)
    .attr("y", legendY + 23)
    .attr("text-anchor", "end")
    .attr("font-size", 10)
    .attr("fill", "#526579")
    .text(formatNumber(maxCases));

  // Unavailable-data key.
  legend
    .append("rect")
    .attr("x", legendX)
    .attr("y", legendY + 32)
    .attr("width", 10)
    .attr("height", 10)
    .attr("rx", 2)
    .attr("fill", "#b8c0c8");

  legend
    .append("text")
    .attr("x", legendX + 15)
    .attr("y", legendY + 41)
    .attr("font-size", 9)
    .attr("fill", "#526579")
    .text("Unavailable");

  updateMapSelection();

  setText("mapMessage", `${mapFeatures.length} district shapes loaded.`);

  console.log(
    "Bangladesh map rendered:",
    mapFeatures.length,
    "district polygons",
  );
}

function selectMapDistrict(feature) {
  const id = getFeatureDistrictId(feature);
  const select = $("districtFilter");

  if (!id || !select) return;

  const matchingOption = [...select.options].find(
    (option) => String(option.value) === String(id),
  );

  if (!matchingOption) {
    console.warn("Map district ID does not match a /districts option:", id);
    return;
  }

  // Update the dropdown and use the same refresh process
  // as a manually selected district.
  select.value = id;
  refreshDashboard();
}

function updateMapSelection() {
  if (!mapSvg) return;

  mapSvg.selectAll("path.district").classed("selected", (feature) => {
    return (
      state.district !== "ALL" &&
      getFeatureDistrictId(feature) === String(state.district)
    );
  });
}

async function loadMap() {
  try {
    if (state.mapData) {
      updateMapSelection();
      return;
    }

    const geojson = await getJSON("/map");

    state.mapData = geojson;
    renderMap(geojson);
  } catch (error) {
    console.error("Map loading failed:", error);

    showEmpty("districtMap", "Could not load Bangladesh district map.");
  }
}

/* -------------------- ALERTS -------------------- */

async function loadAlerts() {
  const rows = await getJSON(`/alerts?limit=500&${currentParams()}`);

  const filtered = filterRows(rows).slice(0, 30);

  renderTable(
    "alertsTable",
    filtered,
    [
      { value: (r) => r.week_start || "—" },
      {
        value: (r) =>
          state.districtNames[r.district_id] || r.district_id || "—",
      },
      { value: (r) => r.disease || "—" },
      {
        value: (r) => (r.suppressed ? "Suppressed" : formatNumber(r.cases)),
        className: "suppressed",
      },
    ],
    "No alert records available.",
    4,
  );
}

/* -------------------- RESOURCES -------------------- */

async function loadResources() {
  const rows = await getJSON(`/resources?${currentParams()}`);

  const filtered = filterRows(rows)
    .slice()
    .sort(
      (a, b) => (Number(b.beds_needed) || 0) - (Number(a.beds_needed) || 0),
    );

  renderTable(
    "resourcesTable",
    filtered,
    [
      { value: (r) => r.district_id || "—" },
      { value: (r) => formatNumber(r.beds_needed) },
      { value: (r) => formatNumber(r.allocated_beds) },
      { value: (r) => formatNumber(r.unmet_beds) },
    ],
    "Resource output unavailable. Run the demand and allocation pipelines.",
    4,
  );
}

/* -------------------- OPTIONAL ENDPOINTS -------------------- */

async function optionalLoad(path, handler, panelId, label) {
  try {
    const data = await getJSON(path);
    handler(data);
    return true;
  } catch (error) {
    console.info(`${label} unavailable:`, error.message);

    if (panelId) {
      showEmpty(panelId, `${label} is not available from the API yet.`);
    }

    return false;
  }
}

/* -------------------- QUALITY REPORT -------------------- */

function displayQuality(data) {
  setText("qualityInputRows", formatNumber(data.input_rows));
  setText("qualityOutputRows", formatNumber(data.output_rows));
  setText("qualityQuarantined", formatNumber(data.quarantined_rows));

  setText(
    "qualityPassRate",
    data.pass_rate == null
      ? "—"
      : `${(Number(data.pass_rate) * 100).toFixed(1)}%`,
  );

  setText("qualityStatus", data.status || "Report loaded");

  renderTable(
    "qualityRulesTable",
    data.rules || [],
    [
      { value: (r) => r.rule },
      { value: (r) => formatNumber(r.passed) },
      { value: (r) => formatNumber(r.failed) },
      {
        value: (r) =>
          r.pass_rate == null
            ? "—"
            : `${(Number(r.pass_rate) * 100).toFixed(1)}%`,
      },
    ],
    "No validation rules supplied.",
    4,
  );
}

/* -------------------- PRIVACY AND LINEAGE -------------------- */

function displayPrivacy(data) {
  setText("suppressedCount", formatNumber(data.suppressed_cells));

  const verified =
    data.suppression_applied_server_side === true &&
    data.aggregated_only === true;

  setText(
    "privacyStatus",
    verified ? "Server controls reported" : "Not verified",
  );

  setText(
    "privacyMessage",
    verified
      ? "The API reports server-side suppression and aggregated-only output. Verify the implementation and authorization independently."
      : "The API has not confirmed server-side suppression and aggregated-only output. Do not treat this dashboard as privacy-compliant.",
  );
}

function displayLineage(data) {
  setText("batchCount", formatNumber(data.batch_count));

  renderTable(
    "lineageTable",
    data.datasets || [],
    [
      { value: (r) => r.dataset },
      { value: (r) => r.bronze_batch_ids },
      { value: (r) => r.loaded_at },
    ],
    "No batch lineage supplied.",
    3,
  );
}

/* -------------------- SEASONAL HEATMAP -------------------- */

function displayHeatmap(rows) {
  const id = "seasonalHeatmap";
  const container = d3.select(`#${id}`);

  container.selectAll("*").remove();

  if (!Array.isArray(rows) || !rows.length) {
    showEmpty(id, "No seasonal heatmap data available.");
    return;
  }

  const data = rows.filter(
    (row) =>
      Number.isFinite(Number(row.week_of_year)) &&
      (row.cases === "Suppressed" ||
        (row.cases !== null &&
          row.cases !== "" &&
          Number.isFinite(Number(row.cases)))),
  );

  if (!data.length) {
    showEmpty(id, "No valid seasonal observations.");
    return;
  }

  const diseases = [...new Set(data.map((row) => row.disease))];
  const weeks = d3.range(1, 54);

  const width = chartDimensions(id, 300).width;
  const left = 110;
  const top = 15;

  const cell = Math.max(4, Math.min(12, (width - left - 15) / 53));

  const height = top + diseases.length * 28 + 35;

  const numericCases = data
    .filter((row) => row.cases !== "Suppressed")
    .map((row) => Number(row.cases));

  const max = d3.max(numericCases) || 1;

  const color = d3.scaleSequential(d3.interpolateYlGnBu).domain([0, max]);

  const svg = container
    .append("svg")
    .attr("viewBox", `0 0 ${width} ${height}`)
    .attr("role", "img")
    .attr("aria-label", "Seasonal disease heatmap");

  const x = d3
    .scaleBand()
    .domain(weeks)
    .range([left, left + cell * 53])
    .padding(0.08);

  const y = d3
    .scaleBand()
    .domain(diseases)
    .range([top, top + diseases.length * 28])
    .padding(0.12);

  svg
    .selectAll("text.disease")
    .data(diseases)
    .join("text")
    .attr("x", left - 7)
    .attr("y", (disease) => y(disease) + y.bandwidth() / 2)
    .attr("text-anchor", "end")
    .attr("dominant-baseline", "central")
    .text((disease) => disease);

  svg
    .selectAll("rect.cell")
    .data(data)
    .join("rect")
    .attr("class", "cell")
    .attr("x", (row) => x(Number(row.week_of_year)))
    .attr("y", (row) => y(row.disease))
    .attr("width", x.bandwidth())
    .attr("height", y.bandwidth())
    .attr("fill", (row) =>
      row.cases === "Suppressed" ? "#9ca3af" : color(Number(row.cases)),
    )
    .on("mousemove", (event, row) =>
      showTooltip(
        event,
        `<strong>${escapeHTML(row.disease)}</strong><br>` +
          `Week: ${row.week_of_year}<br>` +
          `Cases: ${formatNumber(row.cases)}`,
      ),
    )
    .on("mouseleave", hideTooltip);

  svg
    .append("text")
    .attr("x", left)
    .attr("y", height - 5)
    .attr("fill", "#526579")
    .text("Epidemiological week of year");
}

/* -------------------- EARLY WARNING -------------------- */

function displayEarlyWarning(rows) {
  renderTable(
    "earlyWarningTable",
    rows || [],
    [
      { value: (r) => r.rank },
      {
        value: (r) => state.districtNames[r.district_id] || r.district_id,
      },
      { value: (r) => r.disease },
      { value: (r) => r.week_start },
      { value: (r) => formatNumber(r.observed) },
      { value: (r) => formatNumber(r.expected) },
      { value: (r) => r.signal },
    ],
    "No early-warning ranking available.",
    7,
  );
}

/* -------------------- EVALUATION -------------------- */

async function loadEvaluation() {
  const data = await getJSON(`/evaluation?${currentParams()}`);

  const recall = data.event_count
    ? `${(
        (Number(data.detected_count || 0) / Number(data.event_count)) *
        100
      ).toFixed(1)}%`
    : "N/A";

  renderMetricChips("evaluationSummary", [
    ["Reference events", formatNumber(data.event_count)],
    ["Detected events", formatNumber(data.detected_count)],
    ["Recall", recall],
  ]);
}

function displayDetectorComparison(rows) {
  renderTable(
    "detectorComparisonTable",
    rows || [],
    [
      { value: (r) => r.detector },
      {
        value: (r) =>
          r.event_sensitivity == null
            ? "—"
            : `${(Number(r.event_sensitivity) * 100).toFixed(1)}%`,
      },
      {
        value: (r) => formatNumber(r.mean_false_alerts_per_district_year),
      },
      {
        value: (r) =>
          r.median_detection_delay_days == null
            ? "—"
            : `${formatNumber(r.median_detection_delay_days)} days`,
      },
    ],
    "Detector comparison not available.",
    4,
  );
}

/* -------------------- THRESHOLD CURVE -------------------- */

function displayThresholdCurve(rows) {
  const id = "thresholdCurve";
  const container = d3.select(`#${id}`);

  container.selectAll("*").remove();

  if (!rows?.length) {
    showEmpty(id, "Threshold trade-off data not available.");
    return;
  }

  const data = rows
    .map((row) => ({
      threshold: Number(row.z_score_threshold),
      sensitivity: Number(row.event_sensitivity),
      falseAlerts: Number(row.mean_false_alerts_per_district_year),
    }))
    .filter(
      (row) =>
        Number.isFinite(row.threshold) &&
        Number.isFinite(row.sensitivity) &&
        Number.isFinite(row.falseAlerts),
    )
    .sort((a, b) => a.falseAlerts - b.falseAlerts);

  if (!data.length) {
    showEmpty(id, "No valid threshold curve points.");
    return;
  }

  const width = chartDimensions(id, 220).width;
  const height = 220;

  const margin = {
    top: 12,
    right: 15,
    bottom: 38,
    left: 45,
  };

  let xDomain = d3.extent(data, (row) => row.falseAlerts);

  if (xDomain[0] === xDomain[1]) {
    xDomain = [0, xDomain[1] + 1];
  }

  const x = d3
    .scaleLinear()
    .domain(xDomain)
    .nice()
    .range([margin.left, width - margin.right]);

  const y = d3
    .scaleLinear()
    .domain([0, 1])
    .range([height - margin.bottom, margin.top]);

  const svg = container
    .append("svg")
    .attr("viewBox", `0 0 ${width} ${height}`)
    .attr("role", "img")
    .attr("aria-label", "Sensitivity versus false alert tradeoff");

  svg
    .append("g")
    .attr("class", "axis")
    .attr("transform", `translate(0,${height - margin.bottom})`)
    .call(d3.axisBottom(x));

  svg
    .append("g")
    .attr("class", "axis")
    .attr("transform", `translate(${margin.left},0)`)
    .call(d3.axisLeft(y).tickFormat(d3.format(".0%")));

  svg
    .append("path")
    .datum(data)
    .attr("fill", "none")
    .attr("stroke", "#176b64")
    .attr("stroke-width", 2.5)
    .attr(
      "d",
      d3
        .line()
        .x((row) => x(row.falseAlerts))
        .y((row) => y(row.sensitivity))
        .curve(d3.curveMonotoneX),
    );

  svg
    .selectAll("circle")
    .data(data)
    .join("circle")
    .attr("cx", (row) => x(row.falseAlerts))
    .attr("cy", (row) => y(row.sensitivity))
    .attr("r", 3)
    .attr("fill", "#176b64")
    .on("mousemove", (event, row) =>
      showTooltip(
        event,
        `Threshold: ${formatNumber(row.threshold)}<br>` +
          `Sensitivity: ${(row.sensitivity * 100).toFixed(1)}%<br>` +
          `False alerts/district-year: ${formatNumber(row.falseAlerts)}`,
      ),
    )
    .on("mouseleave", hideTooltip);
}

/* -------------------- RESOURCE DEMAND -------------------- */

function displayDemand(data) {
  setText("resourceBeds", formatNumber(data.beds));
  setText("resourceSaline", formatNumber(data.saline_units));

  // The current source has ORS demand, not test-kit demand.
  setText(
    "resourceTestKits",
    data.test_kits == null ? "Not supplied" : formatNumber(data.test_kits),
  );

  setText("resourceUncertainty", data.uncertainty || "Not supplied");

  setText(
    "resourceAssumptions",
    data.assumptions || "No assumptions supplied.",
  );

  renderTable(
    "demandTable",
    data.districts || [],
    [
      { value: (r) => r.district_id },
      { value: (r) => formatNumber(r.beds_needed) },
      { value: (r) => formatNumber(r.saline_units ?? r.saline_needed) },
      { value: (r) => formatNumber(r.test_kits) },
      { value: (r) => formatNumber(r.lower_estimate) },
      { value: (r) => formatNumber(r.upper_estimate) },
    ],
    "No district-level demand estimates.",
    6,
  );
}

/* -------------------- ALLOCATION COMPARISON -------------------- */

function displayAllocation(data) {
  const rows = data.districts || [];

  renderTable(
    "allocationTable",
    rows,
    [
      { value: (r) => r.district_id },
      { value: (r) => formatNumber(r.optimized) },
      { value: (r) => formatNumber(r.baseline) },
      { value: (r) => formatNumber(r.unmet) },
    ],
    "Allocation comparison not available.",
    4,
  );

  const id = "allocationChart";
  const container = d3.select(`#${id}`);

  container.selectAll("*").remove();

  if (!rows.length) {
    showEmpty(id, "No allocation comparison data.");
    return;
  }

  const chartData = rows.slice(0, 15);
  const width = chartDimensions(id, 300).width;
  const height = Math.max(250, chartData.length * 27 + 55);

  const margin = {
    top: 12,
    right: 20,
    bottom: 30,
    left: 85,
  };

  const max =
    d3.max(chartData, (row) =>
      Math.max(Number(row.optimized) || 0, Number(row.baseline) || 0),
    ) || 1;

  const x = d3
    .scaleLinear()
    .domain([0, max])
    .nice()
    .range([margin.left, width - margin.right]);

  const y = d3
    .scaleBand()
    .domain(chartData.map((row) => row.district_id))
    .range([margin.top, height - margin.bottom])
    .padding(0.25);

  const svg = container
    .append("svg")
    .attr("viewBox", `0 0 ${width} ${height}`)
    .attr("role", "img")
    .attr("aria-label", "Optimized versus baseline allocation");

  svg
    .append("g")
    .attr("class", "axis")
    .attr("transform", `translate(0,${height - margin.bottom})`)
    .call(d3.axisBottom(x).ticks(4));

  svg
    .append("g")
    .attr("class", "axis")
    .attr("transform", `translate(${margin.left},0)`)
    .call(d3.axisLeft(y));

  chartData.forEach((row) => {
    const y1 = y(row.district_id);
    const half = y.bandwidth() / 2;

    svg
      .append("rect")
      .attr("x", margin.left)
      .attr("y", y1)
      .attr("width", Math.max(0, x(Number(row.optimized) || 0) - margin.left))
      .attr("height", half - 1)
      .attr("fill", "#176b64");

    svg
      .append("rect")
      .attr("x", margin.left)
      .attr("y", y1 + half)
      .attr("width", Math.max(0, x(Number(row.baseline) || 0) - margin.left))
      .attr("height", half - 1)
      .attr("fill", "#d58a27");
  });
}

/* -------------------- EQUITY CURVE -------------------- */

function displayEquityCurve(rows) {
  const id = "equityCurve";
  const container = d3.select(`#${id}`);

  container.selectAll("*").remove();

  if (!rows?.length) {
    showEmpty(id, "Allocation equity-efficiency curve not available.");
    return;
  }

  const data = rows
    .map((row) => ({
      budget: Number(row.budget),
      efficiency: Number(row.efficiency),
      equity: Number(row.equity),
    }))
    .filter((row) => Object.values(row).every(Number.isFinite))
    .sort((a, b) => a.budget - b.budget);

  if (!data.length) {
    showEmpty(id, "No valid equity curve values.");
    return;
  }

  const width = chartDimensions(id, 220).width;
  const height = 220;

  const margin = {
    top: 12,
    right: 15,
    bottom: 38,
    left: 45,
  };

  let xDomain = d3.extent(data, (row) => row.efficiency);
  let yDomain = d3.extent(data, (row) => row.equity);

  if (xDomain[0] === xDomain[1]) {
    xDomain = [0, xDomain[1] + 1];
  }

  if (yDomain[0] === yDomain[1]) {
    yDomain = [0, yDomain[1] + 1];
  }

  const x = d3
    .scaleLinear()
    .domain(xDomain)
    .nice()
    .range([margin.left, width - margin.right]);

  const y = d3
    .scaleLinear()
    .domain(yDomain)
    .nice()
    .range([height - margin.bottom, margin.top]);

  const svg = container
    .append("svg")
    .attr("viewBox", `0 0 ${width} ${height}`)
    .attr("role", "img")
    .attr("aria-label", "Allocation equity efficiency curve");

  svg
    .append("g")
    .attr("class", "axis")
    .attr("transform", `translate(0,${height - margin.bottom})`)
    .call(d3.axisBottom(x));

  svg
    .append("g")
    .attr("class", "axis")
    .attr("transform", `translate(${margin.left},0)`)
    .call(d3.axisLeft(y));

  svg
    .append("path")
    .datum(data)
    .attr("fill", "none")
    .attr("stroke", "#7970c9")
    .attr("stroke-width", 2.5)
    .attr(
      "d",
      d3
        .line()
        .x((row) => x(row.efficiency))
        .y((row) => y(row.equity))
        .curve(d3.curveMonotoneX),
    );

  svg
    .selectAll("circle")
    .data(data)
    .join("circle")
    .attr("cx", (row) => x(row.efficiency))
    .attr("cy", (row) => y(row.equity))
    .attr("r", 3)
    .attr("fill", "#7970c9")
    .on("mousemove", (event, row) =>
      showTooltip(
        event,
        `Budget: ${formatNumber(row.budget)}<br>` +
          `Efficiency: ${formatNumber(row.efficiency)}<br>` +
          `Equity: ${formatNumber(row.equity)}`,
      ),
    )
    .on("mouseleave", hideTooltip);
}

/* -------------------- PREPAREDNESS CALENDAR -------------------- */

function displayPreparedness(rows) {
  renderTable(
    "preparednessTable",
    rows || [],
    [
      { value: (r) => r.period ?? r.seasonal_week },
      { value: (r) => r.division ?? r.division_name },
      { value: (r) => r.disease_group ?? r.disease },
      { value: (r) => r.expected_risk ?? r.average_weekly_cases },
      { value: (r) => r.action ?? r.recommended_action },
      { value: (r) => r.supplies ?? "Not supplied" },
    ],
    "Preparedness calendar not available.",
    6,
  );
}

/* -------------------- OPTIONAL VIEW LOADING -------------------- */

async function loadOptionalViews() {
  await Promise.all([
    optionalLoad(
      `/data-quality?${currentParams()}`,
      displayQuality,
      null,
      "Data quality report",
    ),

    optionalLoad(`/privacy-status`, displayPrivacy, null, "Privacy status"),

    optionalLoad(`/lineage`, displayLineage, null, "Data lineage"),

    optionalLoad(
      `/seasonal-heatmap?${currentParams()}`,
      displayHeatmap,
      "seasonalHeatmap",
      "Seasonal heatmap",
    ),

    optionalLoad(
      `/early-warning?${currentParams()}`,
      displayEarlyWarning,
      "earlyWarningTable",
      "Early-warning ranking",
    ),

    optionalLoad(
      `/detector-comparison?${currentParams()}`,
      displayDetectorComparison,
      null,
      "Detector comparison",
    ),

    optionalLoad(
      `/threshold-curve?${currentParams()}`,
      displayThresholdCurve,
      "thresholdCurve",
      "Threshold curve",
    ),

    optionalLoad(
      `/resource-demand?${currentParams()}`,
      displayDemand,
      null,
      "Resource demand",
    ),

    optionalLoad(
      `/allocation-comparison?${currentParams()}`,
      displayAllocation,
      "allocationChart",
      "Allocation comparison",
    ),

    optionalLoad(
      `/allocation-curve`,
      displayEquityCurve,
      "equityCurve",
      "Allocation curve",
    ),

    optionalLoad(
      `/preparedness-calendar?${currentParams()}`,
      displayPreparedness,
      null,
      "Preparedness calendar",
    ),
  ]);
}

/* -------------------- REFRESH -------------------- */

async function refreshDashboard() {
  readFilters();

  const version = ++state.refreshId;

  updateMapSelection();

  const jobs = [
    ["summary", loadSummary],
    ["trendChart", loadTrend],
    ["diseaseChart", loadDiseaseChart],
    ["mapTrendChart", loadMultiDiseaseTrend],
    ["districtDiseaseTable", loadDistrictDiseaseCounts],
    ["alertsTable", loadAlerts],
    ["resourcesTable", loadResources],
    ["evaluationSummary", loadEvaluation],
    ["districtMap", loadMap],
    ["optionalViews", loadOptionalViews],
  ];

  const results = await Promise.allSettled(jobs.map(([, fn]) => fn()));

  if (version !== state.refreshId) return;

  results.forEach((result, index) => {
    if (result.status === "rejected") {
      console.error(
        `Dashboard section failed: ${jobs[index][0]}`,
        result.reason,
      );

      const panelId = jobs[index][0];

      if ($(panelId)) {
        showError(
          panelId,
          result.reason?.message || "Could not load this section.",
        );
      }
    }
  });
}

/* -------------------- SCENARIO OPTIMIZATION -------------------- */

function updateScenarioLabels() {
  if ($("budgetValue") && $("budgetSlider")) {
    $("budgetValue").textContent = `${$("budgetSlider").value} beds`;
  }

  if ($("equityValue") && $("equitySlider")) {
    $("equityValue").textContent = `${$("equitySlider").value}%`;
  }

  if ($("thresholdValue") && $("thresholdSlider")) {
    $("thresholdValue").textContent = $("thresholdSlider").value;
  }
}

async function runScenario() {
  const button = $("runScenario");
  const message = $("scenarioMessage");
  const results = $("scenarioResults");

  if (!button || !message || !results) return;

  button.disabled = true;
  button.textContent = "Running...";
  message.textContent = "Optimizing weekly bed allocations...";
  results.replaceChildren();

  const payload = {
    weekly_capacity: Number($("budgetSlider").value),
    equity_floor: Number($("equitySlider").value) / 100,
    detector: $("detectorSelect").value,
    z_score_threshold: Number($("thresholdSlider").value),
  };

  try {
    const response = await fetch(`${API}/scenario`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });

    const data = await response.json();

    if (!response.ok) {
      const detail = data.detail;
      const errorMessage =
        typeof detail === "string"
          ? detail
          : detail?.message || `Scenario failed (${response.status})`;

      const error = new Error(errorMessage);
      error.details = detail;
      throw error;
    }

    message.textContent = "Scenario completed successfully.";

    const summary = document.createElement("div");
    summary.className = "scenario-summary";

    const metrics = [
      ["Forecast weeks", data.forecast_weeks],
      ["Total demand", `${numberFormat.format(data.total_demand)} beds`],
      ["Allocated", `${numberFormat.format(data.total_allocated)} beds`],
      ["Unmet demand", `${numberFormat.format(data.total_unmet)} beds`],
      [
        "Allocation efficiency",
        `${(data.overall_efficiency * 100).toFixed(1)}%`,
      ],
      [
        "Average minimum coverage",
        `${(data.average_weekly_minimum_coverage * 100).toFixed(1)}%`,
      ],
    ];

    for (const [label, value] of metrics) {
      const item = document.createElement("div");
      const heading = document.createElement("strong");
      const detail = document.createElement("span");

      heading.textContent = label;
      detail.textContent = value ?? "?";

      item.append(heading, detail);
      summary.append(item);
    }

    results.append(summary);

    const threshold = document.createElement("p");
    threshold.className = "muted";
    threshold.textContent =
      `Detection: ${data.detector}, threshold ${data.z_score_threshold}. ` +
      `Sensitivity: ${(data.threshold_performance.event_sensitivity * 100).toFixed(1)}%; ` +
      `precision: ${(data.threshold_performance.alert_precision * 100).toFixed(1)}%.`;

    results.append(threshold);

    const table = document.createElement("table");
    table.className = "scenario-table";

    const header = document.createElement("tr");

    for (const label of [
      "Forecast week",
      "Districts",
      "Demand",
      "Allocated",
      "Unmet",
      "Efficiency",
      "Minimum coverage",
    ]) {
      const cell = document.createElement("th");
      cell.textContent = label;
      header.append(cell);
    }

    const thead = document.createElement("thead");
    thead.append(header);

    const tbody = document.createElement("tbody");

    for (const week of data.weekly_results || []) {
      const row = document.createElement("tr");

      const values = [
        week.forecast_week,
        week.represented_districts,
        week.total_demand,
        week.allocated_beds,
        week.unmet_beds,
        `${(week.efficiency * 100).toFixed(1)}%`,
        `${(week.minimum_coverage * 100).toFixed(1)}%`,
      ];

      for (const value of values) {
        const cell = document.createElement("td");
        cell.textContent = value ?? "?";
        row.append(cell);
      }

      tbody.append(row);
    }

    table.append(thead, tbody);
    results.append(table);
  } catch (error) {
    message.textContent = error.message;

    const detail = error.details;

    if (detail && typeof detail === "object") {
      const context = document.createElement("p");
      context.className = "muted";

      context.textContent = [
        detail.forecast_week ? `Week: ${detail.forecast_week}` : "",
        detail.weekly_capacity != null
          ? `Capacity: ${detail.weekly_capacity}`
          : "",
        detail.equity_floor != null
          ? `Equity floor: ${(detail.equity_floor * 100).toFixed(0)}%`
          : "",
        detail.represented_districts != null
          ? `Represented districts: ${detail.represented_districts}`
          : "",
      ]
        .filter(Boolean)
        .join(" ? ");

      results.append(context);
    }

    console.error("Scenario optimization failed:", error);
  } finally {
    button.disabled = false;
    button.textContent = "Run scenario";
  }
}

/* -------------------- INITIALIZATION -------------------- */

async function init() {
  try {
    const health = await getJSON("/health");

    if (health.status !== "ok") {
      throw new Error("API health check did not return status ok.");
    }

    setStatus("API connected", "online");

    await loadFilters();
    await refreshDashboard();
  } catch (error) {
    console.error("Dashboard initialization failed:", error);

    setStatus("API unavailable", "offline");

    showError("trendChart", `Dashboard could not start: ${error.message}`);

    showEmpty(
      "districtMap",
      "District map unavailable because the API could not be reached.",
    );
  }
}

/* -------------------- EVENT LISTENERS -------------------- */

$("applyFilters")?.addEventListener("click", refreshDashboard);

$("districtFilter")?.addEventListener("change", refreshDashboard);

$("diseaseFilter")?.addEventListener("change", refreshDashboard);

$("budgetSlider")?.addEventListener("input", updateScenarioLabels);
$("equitySlider")?.addEventListener("input", updateScenarioLabels);
$("thresholdSlider")?.addEventListener("change", updateScenarioLabels);
$("runScenario")?.addEventListener("click", runScenario);

updateScenarioLabels();

$("resetFilters")?.addEventListener("click", () => {
  if ($("districtFilter")) {
    $("districtFilter").value = "ALL";
  }

  if ($("diseaseFilter")) {
    $("diseaseFilter").value = "ALL";
  }

  if ($("dateFrom")) {
    $("dateFrom").value = "";
  }

  if ($("dateTo")) {
    $("dateTo").value = "";
  }

  refreshDashboard();
});

$("clearMapSelection")?.addEventListener("click", () => {
  if ($("districtFilter")) {
    $("districtFilter").value = "ALL";
  }

  refreshDashboard();
});

/* -------------------- RESPONSIVE RESIZE -------------------- */

let resizeTimer;

window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);

  resizeTimer = setTimeout(async () => {
    if (state.mapData) {
      renderMap(state.mapData);
    }

    await Promise.allSettled([
      loadTrend(),
      loadDiseaseChart(),
      loadMultiDiseaseTrend(),
    ]);
  }, 200);
});

/* -------------------- START -------------------- */

init();
