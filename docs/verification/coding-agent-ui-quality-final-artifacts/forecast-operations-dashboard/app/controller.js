const { useEffect, useState } = ambient.react;
const { Card, Column, Row, Text, Button } = ambient.components;

function validForecast(value) {
  return !!value && typeof value === "object" &&
    typeof value.location === "string" &&
    value.current && typeof value.current === "object" &&
    typeof value.current.temperature === "number" && Number.isFinite(value.current.temperature) &&
    typeof value.current.condition === "string" &&
    Array.isArray(value.days) && value.days.length === 7 &&
    value.days.every((day) => day && typeof day.date === "string" &&
      typeof day.high === "number" && Number.isFinite(day.high) &&
      typeof day.low === "number" && Number.isFinite(day.low) &&
      typeof day.precipitation === "number" && Number.isFinite(day.precipitation)) &&
    Array.isArray(value.services) && value.services.length === 2 &&
    value.services.every((service) => service && typeof service.name === "string" && typeof service.status === "string");
}

function dateLabel(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

export default function App() {
  const [forecast, setForecast] = useState(null);
  const [state, setState] = useState("loading");
  const [expanded, setExpanded] = useState(false);

  async function loadForecast() {
    setState("loading");
    try {
      const response = await ambient.net.request("harbor-weather", { path: "/v1/forecast", method: "GET" });
      const data = response && Object.prototype.hasOwnProperty.call(response, "data") ? response.data : response;
      if (!validForecast(data)) {
        setForecast(null);
        setState("malformed");
        return;
      }
      setForecast(data);
      setState("ready");
    } catch (_) {
      setForecast(null);
      setState("error");
    }
  }

  useEffect(() => { loadForecast(); }, []);

  if (state === "loading") return ambient.html`<${Card} title="Harbor Station"><${Column} gap="12px"><${Text} text="Loading current conditions and forecast…" /><//><//>`;
  if (state === "error" || state === "malformed") return ambient.html`
    <${Card} title="Harbor Station"><${Column} gap="12px">
      <${Text} text=${state === "error" ? "Forecast unavailable. Check your connection and try again." : "The forecast response could not be read. Try again to reload it."} />
      <${Button} onClick=${loadForecast}>Retry<//>
    <//><//>`;

  const days = expanded ? forecast.days : forecast.days.slice(0, 3);
  return ambient.html`
    <${Column} gap="16px">
      <${Card} title="Harbor Station">
        <${Column} gap="12px">
          <${Text} text=${forecast.location} />
          <${Row} gap="12px" align="center">
            <${Text} text=${`${forecast.current.temperature}°`} style=${{ fontSize: "42px", fontWeight: "700" }} />
            <${Column} gap="4px"><${Text} text="Current conditions" /><${Text} text=${forecast.current.condition} /><//>
          </${Row}>
          <${Text} text="Temperature outlook" style=${{ fontWeight: "700" }} />
          <div role="img" aria-label="Seven-day high and low temperature trend" style=${{ display: "grid", gridTemplateColumns: "repeat(7, minmax(0, 1fr))", gap: "6px", alignItems: "end", minHeight: "128px" }}>
            ${forecast.days.map((day) => ambient.html`<div style=${{ display: "flex", flexDirection: "column", alignItems: "center", gap: "5px", minWidth: 0 }}>
              <span style=${{ fontSize: "12px", fontWeight: "700" }}>${day.high}°</span>
              <div aria-hidden="true" style=${{ width: "min(100%, 24px)", height: `${Math.max(16, Math.min(76, (day.high - day.low + 8) * 3))}px`, borderRadius: "999px", background: "linear-gradient(180deg, #f59e0b, #38bdf8)", border: "1px solid currentColor" }} />
              <span style=${{ fontSize: "12px" }}>${day.low}°</span>
              <span style=${{ fontSize: "10px", opacity: 0.75 }}>${dateLabel(day.date)}</span>
            </div>`)}
          </div>
        <//>
      <//>
      <${Card} title="7-day forecast">
        <${Column} gap="8px">
          ${days.map((day) => ambient.html`<${Row} gap="8px" align="center" style=${{ justifyContent: "space-between", borderBottom: "1px solid color-mix(in srgb, currentColor 16%, transparent)", padding: "8px 0" }}>
            <${Text} text=${dateLabel(day.date)} />
            <${Text} text=${`${day.high}° / ${day.low}°`} />
            <${Text} text=${`${day.precipitation}% precip.`} />
          <//>`)}
          <${Button} aria-label=${expanded ? "Show fewer forecast days" : "Show all seven forecast days"} onClick=${() => setExpanded(!expanded)}>${expanded ? "Show less" : "View all seven days"}<//>
        <//>
      <//>
      <${Card} title="Service status">
        <${Column} gap="8px">
          ${forecast.services.map((service) => ambient.html`<${Row} gap="8px" align="center" style=${{ justifyContent: "space-between" }}>
            <${Text} text=${service.name} /><${Text} text=${service.status} style=${{ fontWeight: "700" }} />
          <//>`)}
        <//>
      <//>
    <//>`;
}
