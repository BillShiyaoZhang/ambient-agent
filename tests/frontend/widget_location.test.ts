import { afterEach, describe, expect, it, vi } from "vitest";
import { requestDeviceLocation } from "../../frontend/src/services/widgetLocation";

afterEach(() => vi.unstubAllGlobals());

describe("trusted browser location", () => {
  it("refuses insecure hosts before touching geolocation", async () => {
    const locate = vi.fn();
    vi.stubGlobal("isSecureContext", false);
    vi.stubGlobal("navigator", { geolocation: { getCurrentPosition: locate } });
    await expect(requestDeviceLocation({ timeout: 1000, maximumAge: 0 })).rejects.toMatchObject({ code: "device_location_insecure_context" });
    expect(locate).not.toHaveBeenCalled();
  });

  it("returns only the approved position fields", async () => {
    vi.stubGlobal("isSecureContext", true);
    const locate = vi.fn((success) => success({ coords: { latitude: 31, longitude: 121, accuracy: 20, altitude: 100, speed: 8 }, timestamp: 1720000000000 }));
    vi.stubGlobal("navigator", { geolocation: { getCurrentPosition: locate } });
    await expect(requestDeviceLocation({ timeout: 1000, maximumAge: 0 })).resolves.toEqual({ latitude: 31, longitude: 121, accuracy: 20, timestamp: 1720000000000 });
    expect(locate.mock.calls[0][2]).toEqual({ timeout: 1000, maximumAge: 0, enableHighAccuracy: false });
  });

  it("does not start device acquisition for an already closed session", async () => {
    vi.stubGlobal("isSecureContext", true);
    const locate = vi.fn();
    vi.stubGlobal("navigator", { geolocation: { getCurrentPosition: locate } });
    const controller = new AbortController();
    controller.abort();
    await expect(requestDeviceLocation({ timeout: 1000, maximumAge: 0 }, controller.signal)).rejects.toMatchObject({ code: "device_location_unavailable" });
    expect(locate).not.toHaveBeenCalled();
  });

  it.each([[1, "device_location_denied"], [2, "device_location_unavailable"], [3, "device_location_timeout"]])("maps browser error %s without provider detail", async (code, expectedCode) => {
    vi.stubGlobal("isSecureContext", true);
    vi.stubGlobal("navigator", { geolocation: { getCurrentPosition: (_success: unknown, failure: (error: unknown) => void) => failure({ code, message: "private browser detail" }) } });
    await expect(requestDeviceLocation({ timeout: 1000, maximumAge: 0 })).rejects.toMatchObject({ code: expectedCode });
  });

  it("bounds an unresponsive browser and ignores its late callback", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("isSecureContext", true);
    let callback: (value: unknown) => void = () => undefined;
    vi.stubGlobal("navigator", { geolocation: { getCurrentPosition: (success: typeof callback) => { callback = success; } } });
    const pending = requestDeviceLocation({ timeout: 1000, maximumAge: 0 });
    const assertion = expect(pending).rejects.toMatchObject({ code: "device_location_timeout" });
    await vi.advanceTimersByTimeAsync(1000);
    await assertion;
    callback({ coords: { latitude: 1, longitude: 2, accuracy: 3 }, timestamp: 1 });
    vi.useRealTimers();
  });
});
