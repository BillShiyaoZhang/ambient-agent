const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

function logResult(success, data = {}) {
  console.log(JSON.stringify({ success, ...data }, null, 2));
}

// Arguments: [layout_type] [app_dir]
const args = process.argv.slice(2);
if (args.length < 2) {
  logResult(false, { error: 'Missing arguments. Usage: node validate_runtime.js [a2ui|direct] [app_dir]' });
  process.exit(1);
}

const layoutType = args[0];
const appDir = path.resolve(args[1]);

const controllerPath = path.join(appDir, 'controller.js');
if (!fs.existsSync(controllerPath)) {
  logResult(false, { error: `controller.js not found in ${appDir}` });
  process.exit(1);
}

const controllerCode = fs.readFileSync(controllerPath, 'utf8');

let layoutJson = null;
if (layoutType === 'a2ui') {
  const layoutPath = path.join(appDir, 'layout.json');
  if (!fs.existsSync(layoutPath)) {
    logResult(false, { error: `layout.json not found in ${appDir} (required for A2UI)` });
    process.exit(1);
  }
  try {
    layoutJson = JSON.parse(fs.readFileSync(layoutPath, 'utf8'));
  } catch (err) {
    logResult(false, { error: `Failed to parse layout.json: ${err.message}` });
    process.exit(1);
  }
}

// 1. Setup Mock DOM Environment via JSDOM
const dom = new JSDOM('<!DOCTYPE html><html><body><div id="root"></div></body></html>', {
  runScripts: "outside-only",
  url: "http://localhost/"
});
const { window } = dom;
global.window = window;
global.document = window.document;

const rootElement = window.document.getElementById('root');

// 2. Prepare Mock SDK Interfaces
const registeredEvents = {}; // actionId -> callback
const subscriptions = [];
const mutations = [];
const localState = {};

// Helper to resolve JSON Pointer path in local state
function getPointer(obj, ptr) {
  if (ptr === '' || ptr === '/') return obj;
  const parts = ptr.split('/').slice(1);
  let current = obj;
  for (const part of parts) {
    const key = part.replace(/~1/g, '/').replace(/~0/g, '~');
    if (current === undefined || current === null || typeof current !== 'object') return undefined;
    current = current[key];
  }
  return current;
}

function setPointer(obj, ptr, value) {
  const parts = ptr.split('/').slice(1);
  let current = obj;
  for (let i = 0; i < parts.length - 1; i++) {
    const key = parts[i].replace(/~1/g, '/').replace(/~0/g, '~');
    if (!(key in current) || typeof current[key] !== 'object') {
      current[key] = {};
    }
    current = current[key];
  }
  const lastKey = parts[parts.length - 1].replace(/~1/g, '/').replace(/~0/g, '~');
  current[lastKey] = value;
}

const mockAmbient = {
  state: {
    get: (pointer) => getPointer(localState, pointer),
    set: (pointer, value) => {
      setPointer(localState, pointer, value);
      // Trigger observers (mocked re-render)
      if (mockAmbient.state._observers[pointer]) {
        mockAmbient.state._observers[pointer].forEach(cb => cb(value));
      }
    },
    onChange: (pointer, callback) => {
      if (!mockAmbient.state._observers[pointer]) {
        mockAmbient.state._observers[pointer] = [];
      }
      mockAmbient.state._observers[pointer].push(callback);
    },
    _observers: {}
  },
  graph: {
    subscribe: (query, callback) => {
      subscriptions.push(query);
      // Fire callback with mock empty list immediately to trigger any initial render paths
      setTimeout(() => {
        try {
          callback([]);
        } catch (e) {
          // ignore async errors in initial mock callback to prevent script crashing during init
        }
      }, 0);
      return () => {}; // Unsubscribe function
    },
    mutate: async (actions) => {
      mutations.push(...actions);
      return { success: true };
    }
  },
  ui: {
    on: (event, actionId, callback) => {
      registeredEvents[actionId] = callback;
    }
  },
  sendMessage: (msg) => {
    // Mock message sender
  },
  fullscreen: () => {},
  minimize: () => {}
};

// 3. Simple A2UI Static Layout Validation
const staticErrors = [];
const componentIds = new Set();
if (layoutType === 'a2ui' && Array.isArray(layoutJson)) {
  const allowedTypes = ['Column', 'Row', 'Card', 'Text', 'Button', 'TextField', 'Checkbox', 'List', 'Table'];
  layoutJson.forEach((comp, idx) => {
    if (!comp.id) {
      staticErrors.push(`Component at index ${idx} lacks unique 'id'`);
    } else {
      componentIds.add(comp.id);
    }
    if (!comp.type) {
      staticErrors.push(`Component '${comp.id || idx}' lacks 'type'`);
    } else if (!allowedTypes.includes(comp.type)) {
      staticErrors.push(`Component '${comp.id}' uses unsupported type '${comp.type}'`);
    }
  });

  // Verify child ID references
  layoutJson.forEach(comp => {
    if (comp.children && Array.isArray(comp.children)) {
      comp.children.forEach(childId => {
        if (!componentIds.has(childId)) {
          staticErrors.push(`Container '${comp.id}' references non-existent child '${childId}'`);
        }
      });
    }
  });
}

if (staticErrors.length > 0) {
  logResult(false, { error: 'Static Layout Validation Failed', details: staticErrors });
  process.exit(0);
}

// 4. Load & Execute the Controller
try {
  // Wrap controller in a sandboxed execution function with parameters: root, ambient, fetch
  const runner = new Function('root', 'ambient', 'fetch', controllerCode);
  
  // Define mock fetch
  const mockFetch = async () => new Response();

  runner(rootElement, mockAmbient, mockFetch);

  // Give any microtasks/subscriptions an event loop tick to fire initial callbacks
  setTimeout(async () => {
    // 5. Simulate triggering registered actions to verify event handler correctness
    const eventsTriggered = [];
    const triggerErrors = [];

    for (const [actionId, callback] of Object.entries(registeredEvents)) {
      eventsTriggered.push(actionId);
      try {
        // Trigger with dummy event parameters if they require them
        await Promise.resolve(callback("test-value", 0));
      } catch (err) {
        triggerErrors.push({ actionId, error: err.message, stack: err.stack });
      }
    }

    if (triggerErrors.length > 0) {
      logResult(false, {
        error: 'Event Handler Execution Failed',
        details: triggerErrors,
        subscriptions,
        mutations,
        registeredEvents: Object.keys(registeredEvents)
      });
    } else {
      logResult(true, {
        subscriptions,
        mutations,
        registeredEvents: Object.keys(registeredEvents),
        eventsTriggered,
        stateSnapshot: localState
      });
    }
    process.exit(0);
  }, 10);

} catch (err) {
  logResult(false, {
    error: `Runtime execution failed: ${err.message}`,
    stack: err.stack
  });
  process.exit(0);
}
