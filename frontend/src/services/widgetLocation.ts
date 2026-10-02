export interface LocationOptions { timeout: number; maximumAge: number }
export interface DeviceLocation { latitude: number; longitude: number; accuracy: number; timestamp: number }

const failure = (code: string, message: string) => Object.assign(new Error(message), { code });

export const requestDeviceLocation = (options: LocationOptions, signal?: AbortSignal): Promise<DeviceLocation> => {
  if (!Number.isInteger(options.timeout) || options.timeout < 1000 || options.timeout > 30000
    || !Number.isInteger(options.maximumAge) || options.maximumAge < 0 || options.maximumAge > 300000) {
    return Promise.reject(failure("device_location_request_invalid", "Invalid device location options"));
  }
  if (globalThis.isSecureContext !== true) {
    return Promise.reject(failure("device_location_insecure_context", "Device location requires a secure browser context"));
  }
  if (!globalThis.navigator?.geolocation) {
    return Promise.reject(failure("device_location_unavailable", "Device location is unavailable in this browser"));
  }
  return new Promise((resolve, reject) => {
    let settled = false;
    const finish = (error?: Error, result?: DeviceLocation) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
      if (error) reject(error);
      else resolve(result!);
    };
    const abort = () => finish(failure("device_location_unavailable", "Device location session closed"));
    const timer = setTimeout(() => finish(failure("device_location_timeout", "Device location request timed out")), options.timeout);
    signal?.addEventListener("abort", abort, { once: true });
    if (signal?.aborted) { abort(); return; }
    try {
      navigator.geolocation.getCurrentPosition((position) => {
        const result = { latitude: position.coords.latitude, longitude: position.coords.longitude, accuracy: position.coords.accuracy, timestamp: position.timestamp };
        if (!Object.values(result).every(Number.isFinite) || Math.abs(result.latitude) > 90
          || Math.abs(result.longitude) > 180 || result.accuracy < 0 || result.timestamp < 0) {
          finish(failure("device_location_invalid", "Device returned an invalid location"));
        } else finish(undefined, result);
      }, (error) => {
        const code = error.code === 1 ? "device_location_denied" : error.code === 3 ? "device_location_timeout" : "device_location_unavailable";
        finish(failure(code, error.code === 1 ? "Device location permission was denied" : "Device location could not be acquired"));
      }, { ...options, enableHighAccuracy: false });
    } catch {
      finish(failure("device_location_unavailable", "Device location could not be acquired"));
    }
  });
};
