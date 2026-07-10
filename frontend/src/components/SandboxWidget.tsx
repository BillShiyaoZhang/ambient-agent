import React, { useEffect, useRef } from "react";
import type { Widget } from "./DashboardCanvas";
import wsService from "../services/websocket";

// --- Ambient Web Components (Custom Elements) Definitions ---

class AmbientCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
  }
  connectedCallback() {
    const title = this.getAttribute('title') || '';
    this.shadowRoot!.innerHTML = `
      <style>
        :host {
          display: block;
          background: rgba(255, 255, 255, 0.02);
          border: 1px solid rgba(255, 255, 255, 0.06);
          border-radius: 12px;
          padding: 16px;
          backdrop-filter: blur(16px);
          -webkit-backdrop-filter: blur(16px);
          box-shadow: 0 4px 30px rgba(0, 0, 0, 0.2);
          transition: all 0.3s ease;
        }
        :host(:hover) {
          border-color: rgba(255, 255, 255, 0.1);
          box-shadow: 0 4px 30px rgba(139, 92, 246, 0.15);
        }
        .header {
          border-bottom: 1px solid rgba(255, 255, 255, 0.06);
          padding-bottom: 8px;
          margin-bottom: 12px;
        }
        .title {
          font-size: 0.9rem;
          font-weight: 600;
          color: rgba(255, 255, 255, 0.9);
          margin: 0;
          letter-spacing: 0.025em;
        }
      </style>
      ${title ? `<div class="header"><h3 class="title">${title}</h3></div>` : ''}
      <slot></slot>
    `;
  }
}

class AmbientButton extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
  }
  static get observedAttributes() {
    return ['variant', 'loading', 'disabled'];
  }
  attributeChangedCallback() {
    this.render();
  }
  connectedCallback() {
    this.render();
  }
  render() {
    const variant = this.getAttribute('variant') || 'primary';
    const loading = this.hasAttribute('loading');
    const disabled = this.hasAttribute('disabled') || loading;
    
    let bg = 'rgba(139, 92, 246, 0.2)';
    let border = 'rgba(139, 92, 246, 0.4)';
    let hoverBg = 'rgba(139, 92, 246, 0.35)';
    let hoverBorder = 'rgba(139, 92, 246, 0.6)';
    
    if (variant === 'secondary') {
      bg = 'rgba(255, 255, 255, 0.04)';
      border = 'rgba(255, 255, 255, 0.1)';
      hoverBg = 'rgba(255, 255, 255, 0.08)';
      hoverBorder = 'rgba(255, 255, 255, 0.2)';
    } else if (variant === 'danger') {
      bg = 'rgba(239, 68, 68, 0.15)';
      border = 'rgba(239, 68, 68, 0.3)';
      hoverBg = 'rgba(239, 68, 68, 0.25)';
      hoverBorder = 'rgba(239, 68, 68, 0.5)';
    }

    this.shadowRoot!.innerHTML = `
      <style>
        button {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          padding: 8px 16px;
          font-size: 0.75rem;
          font-weight: 600;
          color: rgba(255, 255, 255, 0.9);
          background: ${bg};
          border: 1px solid ${border};
          border-radius: 8px;
          cursor: ${disabled ? 'not-allowed' : 'pointer'};
          opacity: ${disabled ? '0.6' : '1'};
          transition: all 0.2s ease;
          outline: none;
          gap: 6px;
        }
        button:hover {
          ${disabled ? '' : `
            background: ${hoverBg};
            border-color: ${hoverBorder};
            box-shadow: 0 0 12px rgba(139, 92, 246, 0.2);
            transform: translateY(-1px);
          `}
        }
        button:active {
          ${disabled ? '' : `
            transform: translateY(0);
          `}
        }
        .spinner {
          width: 12px;
          height: 12px;
          border: 2px solid rgba(255,255,255,0.3);
          border-radius: 50%;
          border-top-color: #fff;
          animation: spin 1s ease-in-out infinite;
        }
        @keyframes spin {
          to { transform: rotate(360deg); }
        }
      </style>
      <button ${disabled ? 'disabled' : ''}>
        ${loading ? '<div class="spinner"></div>' : ''}
        <slot></slot>
      </button>
    `;
  }
}

class AmbientInput extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
  }
  static get observedAttributes() {
    return ['placeholder', 'type', 'value', 'disabled'];
  }
  attributeChangedCallback() {
    this.render();
  }
  connectedCallback() {
    this.render();
  }
  get value() {
    const input = this.shadowRoot!.querySelector('input');
    return input ? input.value : '';
  }
  set value(val: string) {
    this.setAttribute('value', val);
    const input = this.shadowRoot!.querySelector('input');
    if (input) input.value = val;
  }
  render() {
    const placeholder = this.getAttribute('placeholder') || '';
    const type = this.getAttribute('type') || 'text';
    const value = this.getAttribute('value') || '';
    const disabled = this.hasAttribute('disabled');

    this.shadowRoot!.innerHTML = `
      <style>
        input {
          width: 100%;
          box-sizing: border-box;
          padding: 8px 12px;
          font-size: 0.75rem;
          color: rgba(255, 255, 255, 0.9);
          background: rgba(255, 255, 255, 0.02);
          border: 1px solid rgba(255, 255, 255, 0.08);
          border-radius: 8px;
          outline: none;
          transition: all 0.2s ease;
        }
        input:focus {
          border-color: rgba(139, 92, 246, 0.6);
          background: rgba(255, 255, 255, 0.04);
          box-shadow: 0 0 8px rgba(139, 92, 246, 0.15);
        }
        input:disabled {
          opacity: 0.5;
          cursor: not-allowed;
        }
      </style>
      <input type="${type}" placeholder="${placeholder}" value="${value}" ${disabled ? 'disabled' : ''} />
    `;
    
    const inputEl = this.shadowRoot!.querySelector('input');
    if (inputEl) {
      inputEl.addEventListener('input', (e: any) => {
        this.setAttribute('value', e.target.value);
        this.dispatchEvent(new CustomEvent('input', { bubbles: true, composed: true }));
      });
      inputEl.addEventListener('change', () => {
        this.dispatchEvent(new CustomEvent('change', { bubbles: true, composed: true }));
      });
    }
  }
}

class AmbientSelect extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
  }
  static get observedAttributes() {
    return ['disabled', 'value'];
  }
  attributeChangedCallback() {
    this.render();
  }
  connectedCallback() {
    this.render();
  }
  get value() {
    const select = this.shadowRoot!.querySelector('select');
    return select ? select.value : '';
  }
  set value(val: string) {
    this.setAttribute('value', val);
    const select = this.shadowRoot!.querySelector('select');
    if (select) select.value = val;
  }
  render() {
    const disabled = this.hasAttribute('disabled');
    const value = this.getAttribute('value') || '';

    const optionsHTML = Array.from(this.querySelectorAll('option'))
      .map(opt => `<option value="${opt.value}" ${opt.value === value ? 'selected' : ''}>${opt.textContent}</option>`)
      .join('');

    this.shadowRoot!.innerHTML = `
      <style>
        select {
          width: 100%;
          padding: 8px 12px;
          font-size: 0.75rem;
          color: rgba(255, 255, 255, 0.9);
          background: rgba(255, 255, 255, 0.02);
          border: 1px solid rgba(255, 255, 255, 0.08);
          border-radius: 8px;
          outline: none;
          cursor: pointer;
          transition: all 0.2s ease;
        }
        select:focus {
          border-color: rgba(139, 92, 246, 0.6);
          background: rgba(255, 255, 255, 0.04);
        }
        select:disabled {
          opacity: 0.5;
          cursor: not-allowed;
        }
        option {
          background-color: #120f1d;
          color: #fff;
        }
      </style>
      <select ${disabled ? 'disabled' : ''}>
        ${optionsHTML}
      </select>
    `;

    const selectEl = this.shadowRoot!.querySelector('select');
    if (selectEl) {
      selectEl.addEventListener('change', (e: any) => {
        this.setAttribute('value', e.target.value);
        this.dispatchEvent(new CustomEvent('change', { bubbles: true, composed: true }));
      });
    }
  }
}

class AmbientBadge extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
  }
  static get observedAttributes() {
    return ['variant'];
  }
  attributeChangedCallback() {
    this.render();
  }
  connectedCallback() {
    this.render();
  }
  render() {
    const variant = this.getAttribute('variant') || 'info';
    
    let bg = 'rgba(59, 130, 246, 0.15)';
    let color = 'rgba(147, 197, 253, 0.95)';
    let border = 'rgba(59, 130, 246, 0.25)';

    if (variant === 'success') {
      bg = 'rgba(16, 185, 129, 0.15)';
      color = 'rgba(110, 231, 183, 0.95)';
      border = 'rgba(16, 185, 129, 0.25)';
    } else if (variant === 'warning') {
      bg = 'rgba(245, 158, 11, 0.15)';
      color = 'rgba(252, 211, 77, 0.95)';
      border = 'rgba(245, 158, 11, 0.25)';
    } else if (variant === 'danger') {
      bg = 'rgba(239, 68, 68, 0.15)';
      color = 'rgba(252, 165, 165, 0.95)';
      border = 'rgba(239, 68, 68, 0.25)';
    }

    this.shadowRoot!.innerHTML = `
      <style>
        span {
          display: inline-block;
          padding: 2px 8px;
          font-size: 0.65rem;
          font-weight: 600;
          color: ${color};
          background: ${bg};
          border: 1px solid ${border};
          border-radius: 9999px;
          letter-spacing: 0.025em;
        }
      </style>
      <span>
        <slot></slot>
      </span>
    `;
  }
}

class AmbientTable extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
  }
  connectedCallback() {
    this.shadowRoot!.innerHTML = `
      <style>
        :host {
          display: block;
          width: 100%;
          overflow-x: auto;
          background: rgba(255, 255, 255, 0.01);
          border: 1px solid rgba(255, 255, 255, 0.05);
          border-radius: 8px;
        }
        table {
          width: 100%;
          border-collapse: collapse;
          font-size: 0.75rem;
          text-align: left;
        }
        ::slotted(thead) {
          background: rgba(255, 255, 255, 0.03);
          border-bottom: 1px solid rgba(255, 255, 255, 0.08);
        }
        ::slotted(th) {
          padding: 10px 12px;
          font-weight: 600;
          color: rgba(255, 255, 255, 0.7);
        }
        ::slotted(td) {
          padding: 10px 12px;
          color: rgba(255, 255, 255, 0.85);
          border-bottom: 1px solid rgba(255, 255, 255, 0.04);
        }
        ::slotted(tr:hover) {
          background: rgba(255, 255, 255, 0.02);
        }
      </style>
      <table>
        <slot></slot>
      </table>
    `;
  }
}

class AmbientChart extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
  }
  static get observedAttributes() {
    return ['type', 'labels', 'data'];
  }
  attributeChangedCallback() {
    this.draw();
  }
  connectedCallback() {
    this.draw();
  }
  draw() {
    const type = this.getAttribute('type') || 'bar';
    const labelsAttr = this.getAttribute('labels') || '';
    const dataAttr = this.getAttribute('data') || '';

    const labels = labelsAttr ? labelsAttr.split(',').map(s => s.trim()) : [];
    const data = dataAttr ? dataAttr.split(',').map(s => parseFloat(s.trim())) : [];

    this.shadowRoot!.innerHTML = `
      <style>
        :host {
          display: block;
          width: 100%;
          background: rgba(255, 255, 255, 0.01);
          border: 1px solid rgba(255, 255, 255, 0.05);
          border-radius: 12px;
          padding: 12px;
          box-sizing: border-box;
        }
        .chart-container {
          position: relative;
          width: 100%;
          height: 140px;
        }
        canvas {
          width: 100%;
          height: 100%;
        }
      </style>
      <div class="chart-container">
        <canvas id="chartCanvas"></canvas>
      </div>
    `;

    const canvas = this.shadowRoot!.getElementById('chartCanvas') as HTMLCanvasElement | null;
    if (!canvas) return;

    requestAnimationFrame(() => {
      const dpr = window.devicePixelRatio || 1;
      const rect = canvas.getBoundingClientRect();
      canvas.width = rect.width * dpr;
      canvas.height = rect.height * dpr;
      const ctx = canvas.getContext('2d');
      if (!ctx) return;
      ctx.scale(dpr, dpr);

      const w = rect.width;
      const h = rect.height;

      ctx.clearRect(0, 0, w, h);

      if (data.length === 0) return;

      const maxVal = Math.max(...data, 1) * 1.1;
      const len = data.length;

      if (type === 'line') {
        const padding = 20;
        const graphW = w - padding * 2;
        const graphH = h - padding * 2;

        ctx.strokeStyle = 'rgba(139, 92, 246, 0.8)';
        ctx.lineWidth = 3;
        ctx.shadowColor = 'rgba(139, 92, 246, 0.5)';
        ctx.shadowBlur = 8;
        
        ctx.beginPath();
        for (let i = 0; i < len; i++) {
          const x = padding + (i / (len - 1 || 1)) * graphW;
          const y = h - padding - (data[i] / maxVal) * graphH;
          if (i === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }
        ctx.stroke();
        
        ctx.shadowBlur = 0;

        ctx.lineTo(padding + graphW, h - padding);
        ctx.lineTo(padding, h - padding);
        ctx.closePath();
        const gradient = ctx.createLinearGradient(0, padding, 0, h - padding);
        gradient.addColorStop(0, 'rgba(139, 92, 246, 0.25)');
        gradient.addColorStop(1, 'rgba(139, 92, 246, 0.0)');
        ctx.fillStyle = gradient;
        ctx.fill();

        for (let i = 0; i < len; i++) {
          const x = padding + (i / (len - 1 || 1)) * graphW;
          const y = h - padding - (data[i] / maxVal) * graphH;

          ctx.fillStyle = '#fff';
          ctx.beginPath();
          ctx.arc(x, y, 4, 0, Math.PI * 2);
          ctx.fill();

          if (labels[i]) {
            ctx.fillStyle = 'rgba(255, 255, 255, 0.4)';
            ctx.font = '9px system-ui';
            ctx.textAlign = 'center';
            ctx.fillText(labels[i], x, h - 4);
          }
        }
      } else {
        const padding = 20;
        const graphW = w - padding * 2;
        const graphH = h - padding * 2;
        const barSpacing = graphW / len;
        const barW = Math.max(2, barSpacing * 0.6);

        for (let i = 0; i < len; i++) {
          const x = padding + i * barSpacing + (barSpacing - barW) / 2;
          const barH = (data[i] / maxVal) * graphH;
          const y = h - padding - barH;

          ctx.fillStyle = 'rgba(139, 92, 246, 0.4)';
          ctx.strokeStyle = 'rgba(139, 92, 246, 0.7)';
          ctx.lineWidth = 1;

          ctx.beginPath();
          (ctx as any).roundRect(x, y, barW, barH, [4, 4, 0, 0]);
          ctx.fill();
          ctx.stroke();

          if (labels[i]) {
            ctx.fillStyle = 'rgba(255, 255, 255, 0.4)';
            ctx.font = '9px system-ui';
            ctx.textAlign = 'center';
            ctx.fillText(labels[i], x + barW / 2, h - 4);
          }
        }
      }
    });
  }
}

const registerCustomElements = () => {
  if (typeof window === 'undefined') return;
  if (!customElements.get('a-card')) customElements.define('a-card', AmbientCard);
  if (!customElements.get('a-button')) customElements.define('a-button', AmbientButton);
  if (!customElements.get('a-input')) customElements.define('a-input', AmbientInput);
  if (!customElements.get('a-select')) customElements.define('a-select', AmbientSelect);
  if (!customElements.get('a-badge')) customElements.define('a-badge', AmbientBadge);
  if (!customElements.get('a-table')) customElements.define('a-table', AmbientTable);
  if (!customElements.get('a-chart')) customElements.define('a-chart', AmbientChart);
};

registerCustomElements();

// --- SandboxWidget Prop Interface ---

interface SandboxWidgetProps {
  widget: Widget;
  onFullscreen?: (id: string) => void;
  onMinimize?: (id: string) => void;
}

// Global fetch cache to share API responses across all mounts and sandbox instances
const globalFetchCache = new Map<string, { data: any; timestamp: number }>();
const CACHE_TTL = 5 * 60 * 1000; // 5 minutes TTL

// Scopes widget-defined CSS rules to prevent global layout pollution
const scopeCss = (css: string, scopeId: string): string => {
  if (!css) return "";
  const cleanCss = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const prefix = `[data-widget-scope="${scopeId}"]`;
  
  return cleanCss.replace(/(^|}|;|{)\s*([^{}@]+)\s*(?={)/g, (match, p1, selector) => {
    const trimmed = selector.trim();
    if (!trimmed) return match;
    
    const scopedSelectors = trimmed.split(",").map((sel: string) => {
      const s = sel.trim();
      if (!s) return "";
      if (s === "from" || s === "to" || s.endsWith("%") || s.includes(prefix)) {
        return s;
      }
      if (s === "html" || s === "body" || s === ":root") {
        return prefix;
      }
      return `${prefix} ${s}`;
    });
    
    return p1 + " " + scopedSelectors.join(", ");
  });
};

export const SandboxWidget: React.FC<SandboxWidgetProps> = ({
  widget,
  onFullscreen,
  onMinimize,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const onFullscreenRef = useRef(onFullscreen);
  const onMinimizeRef = useRef(onMinimize);

  useEffect(() => {
    onFullscreenRef.current = onFullscreen;
    onMinimizeRef.current = onMinimize;
  }, [onFullscreen, onMinimize]);

  useEffect(() => {
    if (!containerRef.current) return;

    // Clear previous contents
    containerRef.current.innerHTML = "";

    // 1. Setup scope attribute
    const scopeId = `widget-${widget.id}`;
    containerRef.current.setAttribute("data-widget-scope", scopeId);

    // 2. Append scoped CSS style block
    if (widget.css) {
      const styleEl = document.createElement("style");
      styleEl.textContent = scopeCss(widget.css, scopeId);
      containerRef.current.appendChild(styleEl);
    }

    // 3. Create HTML content container
    const contentEl = document.createElement("div");
    contentEl.className = "widget-root-content w-full h-full";
    contentEl.innerHTML = widget.html || "";
    containerRef.current.appendChild(contentEl);

    // Track event listeners created inside the SDK to clean them up on unmount
    const customListeners: { event: string; handler: EventListener }[] = [];

    const API_BASE = `http://${window.location.hostname}:8000`;

    // 4. Construct ambient SDK
    const ambient = {
      model: {
        get: async () => {
          const res = await fetch(`${API_BASE}/api/apps/${widget.id}/data`);
          if (!res.ok) throw new Error("Failed to load app data");
          return res.json();
        },
        set: async (data: any) => {
          const res = await fetch(`${API_BASE}/api/apps/${widget.id}/data`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data),
          });
          if (!res.ok) throw new Error("Failed to save app data");
          return res.json();
        },
        onChange: (callback: (data: any) => void) => {
          const handler = (e: Event) => {
            callback((e as CustomEvent).detail);
          };
          const eventName = `app_data_update:${widget.id}`;
          window.addEventListener(eventName, handler);
          customListeners.push({ event: eventName, handler });
        },
      },
      sendMessage: (text: string) => {
        wsService.sendMessage({
          sender: "user",
          content: text,
        });
      },
      fullscreen: () => {
        if (onFullscreenRef.current) {
          onFullscreenRef.current(widget.id);
        }
      },
      minimize: () => {
        if (onMinimizeRef.current) {
          onMinimizeRef.current(widget.id);
        }
      },
      ui: {
        card: (props: { title?: string; children?: string | HTMLElement | HTMLElement[] }) => {
          const el = document.createElement('a-card');
          if (props.title) el.setAttribute('title', props.title);
          if (props.children) {
            if (typeof props.children === 'string') {
              el.innerHTML = props.children;
            } else if (Array.isArray(props.children)) {
              props.children.forEach(c => el.appendChild(c));
            } else {
              el.appendChild(props.children);
            }
          }
          return el;
        },
        button: (props: { label: string; onClick?: () => void; variant?: string; loading?: boolean; disabled?: boolean }) => {
          const el = document.createElement('a-button') as any;
          el.textContent = props.label;
          if (props.variant) el.setAttribute('variant', props.variant);
          if (props.loading) el.setAttribute('loading', '');
          if (props.disabled) el.setAttribute('disabled', '');
          if (props.onClick) el.addEventListener('click', props.onClick);
          return el;
        },
        input: (props: { placeholder?: string; type?: string; value?: string; onChange?: (val: string) => void }) => {
          const el = document.createElement('a-input') as any;
          if (props.placeholder) el.setAttribute('placeholder', props.placeholder);
          if (props.type) el.setAttribute('type', props.type);
          if (props.value !== undefined) el.setAttribute('value', props.value);
          if (props.onChange) {
            el.addEventListener('input', (e: Event) => props.onChange!((e.target as any).value));
          }
          return el;
        },
        select: (props: { options: { value: string; label: string }[]; value?: string; onChange?: (val: string) => void }) => {
          const el = document.createElement('a-select') as any;
          props.options.forEach(opt => {
            const optionEl = document.createElement('option');
            optionEl.value = opt.value;
            optionEl.textContent = opt.label;
            el.appendChild(optionEl);
          });
          if (props.value !== undefined) el.setAttribute('value', props.value);
          if (props.onChange) {
            el.addEventListener('change', (e: Event) => props.onChange!((e.target as any).value));
          }
          return el;
        },
        badge: (props: { label: string; variant?: string }) => {
          const el = document.createElement('a-badge');
          el.textContent = props.label;
          if (props.variant) el.setAttribute('variant', props.variant);
          return el;
        },
        table: (props: { headers: string[]; rows: (string | HTMLElement)[][] }) => {
          const el = document.createElement('a-table');
          const thead = document.createElement('thead');
          const trHead = document.createElement('tr');
          props.headers.forEach(h => {
            const th = document.createElement('th');
            th.textContent = h;
            trHead.appendChild(th);
          });
          thead.appendChild(trHead);
          el.appendChild(thead);

          const tbody = document.createElement('tbody');
          props.rows.forEach(r => {
            const tr = document.createElement('tr');
            r.forEach(cell => {
              const td = document.createElement('td');
              if (cell instanceof HTMLElement) {
                td.appendChild(cell);
              } else {
                td.textContent = String(cell);
              }
              tr.appendChild(td);
            });
            tbody.appendChild(tr);
          });
          el.appendChild(tbody);
          return el;
        },
        chart: (props: { type?: string; labels: string[]; data: number[] }) => {
          const el = document.createElement('a-chart');
          if (props.type) el.setAttribute('type', props.type);
          el.setAttribute('labels', props.labels.join(','));
          el.setAttribute('data', props.data.join(','));
          return el;
        }
      }
    };

    // Custom fetch interceptor to cache external JSON API requests
    const customFetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : (input as any).url || input.toString();
      const isGet = !init || !init.method || init.method.toUpperCase() === "GET";
      const isExternal = url.startsWith("http") && !url.includes(window.location.hostname);
      
      if (isGet && isExternal) {
        const cached = globalFetchCache.get(url);
        if (cached && Date.now() - cached.timestamp < CACHE_TTL) {
          console.log(`[Sandbox Fetch Cache] Hit for ${url}`);
          return new Response(JSON.stringify(cached.data), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
      }
      
      const res = await window.fetch(input, init);
      
      if (isGet && isExternal && res.ok) {
        try {
          const clone = res.clone();
          const json = await clone.json();
          globalFetchCache.set(url, { data: json, timestamp: Date.now() });
        } catch (e) {
          // Ignore non-JSON or clone failures
        }
      }
      return res;
    };

    // 5. Run JS in isolated function scope passing root element, ambient SDK, and cached fetch
    if (widget.js) {
      try {
        const runScript = new Function("root", "ambient", "fetch", widget.js);
        runScript(contentEl, ambient, customFetch);
      } catch (err) {
        console.error(`JS error in widget ${widget.id}:`, err);
        const errorEl = document.createElement("div");
        errorEl.className = "text-red-400 text-xs p-2 mt-2 bg-red-950/20 border border-red-900/30 rounded";
        errorEl.textContent = `Widget JS Error: ${(err as Error).message}`;
        containerRef.current.appendChild(errorEl);
      }
    }

    // Clean up event listeners on unmount
    return () => {
      customListeners.forEach(({ event, handler }) => {
        window.removeEventListener(event, handler);
      });
    };
  }, [widget.id, widget.html, widget.css, widget.js]);

  return (
    <div
      ref={containerRef}
      id={widget.id}
      data-testid={`sandbox-${widget.id}`}
      className="w-full h-full text-white/90 overflow-auto"
    />
  );
};
