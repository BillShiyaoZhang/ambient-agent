import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { DashboardCanvas, Widget } from "../../frontend/src/components/DashboardCanvas";
import { SandboxWidget } from "../../frontend/src/components/SandboxWidget";
import React from "react";

describe("SandboxWidget Rendering & Containment", () => {
  it("should render widget HTML correctly", () => {
    const mockWidget: Widget = {
      id: "test-w1",
      title: "Test Widget",
      html: '<h3 data-testid="widget-title">Inside Widget</h3>',
      css: ".widget-root-content { color: purple; }",
      js: "",
    };

    render(
      <DashboardCanvas
        widgets={[mockWidget]}
        onRemoveWidget={() => {}}
        renderWidgetContent={(w) => <SandboxWidget widget={w} />}
      />
    );

    const titleEl = screen.getByTestId("widget-title");
    expect(titleEl).toBeDefined();
    expect(titleEl.textContent).toBe("Inside Widget");
  });

  it("should scope CSS styles inside the widget container", () => {
    const mockWidget: Widget = {
      id: "test-w2",
      title: "CSS Test",
      html: '<div class="test-class">Styled text</div>',
      css: "[data-widget-scope='widget-test-w2'] .test-class { color: rgb(255, 0, 0); }",
      js: "",
    };

    render(
      <DashboardCanvas
        widgets={[mockWidget]}
        onRemoveWidget={() => {}}
        renderWidgetContent={(w) => <SandboxWidget widget={w} />}
      />
    );

    const sandbox = screen.getByTestId("sandbox-test-w2");
    const styleEl = sandbox.querySelector("style");
    expect(styleEl).toBeDefined();
    expect(styleEl?.textContent).toContain("[data-widget-scope='widget-test-w2']");
  });

  it("should execute JS safely and bind variables to the scoped root element", () => {
    const mockWidget: Widget = {
      id: "test-w3",
      title: "JS Test",
      html: '<button id="btn" data-testid="test-btn">Click me</button><div id="output" data-testid="test-out">Init</div>',
      css: "",
      js: `
        const btn = root.querySelector("#btn");
        const out = root.querySelector("#output");
        btn.addEventListener("click", () => {
          out.textContent = "Clicked!";
        });
      `,
    };

    render(
      <DashboardCanvas
        widgets={[mockWidget]}
        onRemoveWidget={() => {}}
        renderWidgetContent={(w) => <SandboxWidget widget={w} />}
      />
    );

    const btn = screen.getByTestId("test-btn");
    const out = screen.getByTestId("test-out");
    expect(out.textContent).toBe("Init");

    btn.click();
    expect(out.textContent).toBe("Clicked!");
  });

  it("should isolate JS execution context so local variables do not bleed to global window object", () => {
    const mockWidget: Widget = {
      id: "test-w4",
      title: "Isolation Test",
      html: "<div></div>",
      css: "",
      js: "var mySecretVariableBleed = 'secret-value'; window.mySecretVariablePublic = 'public-value';",
    };

    render(
      <DashboardCanvas
        widgets={[mockWidget]}
        onRemoveWidget={() => {}}
        renderWidgetContent={(w) => <SandboxWidget widget={w} />}
      />
    );

    // var inside new Function executes in local scope and does not attach to window (unlike global scripts)
    expect((window as any).mySecretVariableBleed).toBeUndefined();
    // explicit window writing will bleed (which is normal), but let's confirm var does not.
    expect((window as any).mySecretVariablePublic).toBe("public-value");
    
    
    // Clean up
    delete (window as any).mySecretVariablePublic;
  });

  it("should intercept and cache GET requests to external APIs using injected fetch", async () => {
    const fetchSpy = vi.fn().mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify({ weather: "sunny" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      )
    );
    const originalFetch = window.fetch;
    window.fetch = fetchSpy;

    const mockWidget: Widget = {
      id: "test-fetch-cache",
      title: "Fetch Cache Test",
      html: '<div id="output" data-testid="fetch-out">Init</div>',
      css: "",
      js: `
        (async () => {
          const res1 = await fetch("https://api.external.com/weather");
          const data1 = await res1.json();
          
          const res2 = await fetch("https://api.external.com/weather");
          const data2 = await res2.json();
          
          root.querySelector("#output").textContent = data1.weather + "-" + data2.weather;
        })();
      `,
    };

    render(
      <DashboardCanvas
        widgets={[mockWidget]}
        onRemoveWidget={() => {}}
        renderWidgetContent={(w) => <SandboxWidget widget={w} />}
        fullscreenAppId={null}
      />
    );

    // Wait for the async JS execution to complete and update DOM
    await new Promise((resolve) => setTimeout(resolve, 50));

    const out = screen.getByTestId("fetch-out");
    expect(out.textContent).toBe("sunny-sunny");

    // The fetch function should only have been called ONCE due to caching!
    expect(fetchSpy).toHaveBeenCalledTimes(1);

    // Restore fetch
    window.fetch = originalFetch;
  });

  it("should support systematic event bindings on A2UI components (List, Table, TextField, Checkbox, layout containers, Text)", async () => {
    const layout = [
      {
        "id": "root",
        "type": "Column",
        "props": { "style": { "padding": "10px" } },
        "events": { "onClick": { "actionId": "col-clicked" } },
        "children": [
          "test-btn", "test-txt", "test-input", "test-chk", "test-list", "test-table",
          "res-col", "res-btn", "res-txt", "res-input", "res-chk", "res-list", "res-table"
        ]
      },
      {
        "id": "test-btn",
        "type": "Button",
        "props": { "label": "Click Button" },
        "events": { "onClick": { "actionId": "btn-clicked" } }
      },
      {
        "id": "test-txt",
        "type": "Text",
        "props": { "text": "Click Text", "style": { "color": "blue" } },
        "events": { "onClick": { "actionId": "txt-clicked" } }
      },
      {
        "id": "test-input",
        "type": "TextField",
        "props": { "label": "NameInput", "placeholder": "Enter name...", "value": { "binding": "/nameVal" } },
        "events": { "onEnter": { "actionId": "input-entered" } }
      },
      {
        "id": "test-chk",
        "type": "Checkbox",
        "props": { "label": "AcceptCheckbox", "checked": { "binding": "/acceptVal" } },
        "events": { "onChange": { "actionId": "chk-changed" } }
      },
      {
        "id": "test-list",
        "type": "List",
        "props": { "items": { "binding": "/listItems" } },
        "events": { "onItemClick": { "actionId": "list-item-clicked" } }
      },
      {
        "id": "test-table",
        "type": "Table",
        "props": { "columns": ["Name", "Score"], "rows": { "binding": "/tableRows" } },
        "events": { "onRowClick": { "actionId": "table-row-clicked" } }
      },
      {
        "id": "res-col",
        "type": "Text",
        "props": { "text": { "binding": "/colResult" } }
      },
      {
        "id": "res-btn",
        "type": "Text",
        "props": { "text": { "binding": "/btnResult" } }
      },
      {
        "id": "res-txt",
        "type": "Text",
        "props": { "text": { "binding": "/txtResult" } }
      },
      {
        "id": "res-input",
        "type": "Text",
        "props": { "text": { "binding": "/inputResult" } }
      },
      {
        "id": "res-chk",
        "type": "Text",
        "props": { "text": { "binding": "/chkResult" } }
      },
      {
        "id": "res-list",
        "type": "Text",
        "props": { "text": { "binding": "/listResult" } }
      },
      {
        "id": "res-table",
        "type": "Text",
        "props": { "text": { "binding": "/tableResult" } }
      }
    ];

    const js = `
      ambient.state.set('/listItems', [{ label: 'Item A' }, { label: 'Item B' }]);
      ambient.state.set('/tableRows', [['Alice', '100'], ['Bob', '90']]);
      
      ambient.ui.on('click', 'col-clicked', () => {
        ambient.state.set('/colResult', 'colClicked');
      });
      ambient.ui.on('click', 'btn-clicked', () => {
        ambient.state.set('/btnResult', 'btnClicked');
      });
      ambient.ui.on('click', 'txt-clicked', () => {
        ambient.state.set('/txtResult', 'txtClicked');
      });
      ambient.ui.on('click', 'input-entered', (val) => {
        ambient.state.set('/inputResult', val);
      });
      ambient.ui.on('change', 'chk-changed', (checked) => {
        ambient.state.set('/chkResult', checked ? 'checkedTrue' : 'checkedFalse');
      });
      ambient.ui.on('click', 'list-item-clicked', (item, idx) => {
        ambient.state.set('/listResult', item.label + '-' + idx);
      });
      ambient.ui.on('click', 'table-row-clicked', (row, idx) => {
        ambient.state.set('/tableResult', row[0] + '-' + idx);
      });
    `;

    const mockWidget: Widget = {
      id: "a2ui-test",
      title: "A2UI Test",
      layout: JSON.stringify(layout),
      js: js,
    };

    render(<SandboxWidget widget={mockWidget} />);

    // Give functions a split second to register/initialize
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });

    // 1. Test Button click
    const btn = screen.getByText("Click Button");
    await act(async () => {
      btn.click();
    });
    
    // 2. Test Text click
    const txt = screen.getByText("Click Text");
    await act(async () => {
      txt.click();
    });

    // 3. Test Column click
    // Note: column contains padding of 10px and has the ID root or test-id
    const col = screen.getByTestId("sandbox-a2ui-test").firstElementChild;
    if (col) {
      await act(async () => {
        fireEvent.click(col);
      });
    }

    // 4. Test TextField enter
    const input = screen.getByPlaceholderText("Enter name...");
    await act(async () => {
      fireEvent.change(input, { target: { value: "HelloEnter" } });
      fireEvent.keyDown(input, { key: "Enter", code: "Enter", charCode: 13 });
    });

    // 5. Test Checkbox change
    const chk = screen.getByRole("checkbox");
    await act(async () => {
      fireEvent.click(chk);
    });

    // 6. Test List item click
    const listItem = screen.getByText("Item B");
    await act(async () => {
      listItem.click();
    });

    // 7. Test Table row click
    const tableRow = screen.getByText("Bob");
    await act(async () => {
      tableRow.click();
    });

    // Give state updates time to propagate to React view
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });

    // 8. Verify all state updates rendered into result text components
    expect(screen.getByText("colClicked")).toBeDefined();
    expect(screen.getByText("btnClicked")).toBeDefined();
    expect(screen.getByText("txtClicked")).toBeDefined();
    expect(screen.getByText("HelloEnter")).toBeDefined();
    expect(screen.getByText("checkedTrue")).toBeDefined();
    expect(screen.getByText("Item B-1")).toBeDefined();
    expect(screen.getByText("Bob-1")).toBeDefined();
  });
});
