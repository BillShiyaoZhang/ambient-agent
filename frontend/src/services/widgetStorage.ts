export const MAX_WIDGET_STORAGE_KEY_LENGTH = 256;
export const MAX_WIDGET_STORAGE_VALUE_BYTES = 64 * 1024;
export const MAX_WIDGET_STORAGE_ENTRIES = 128;
export const MAX_WIDGET_STORAGE_TOTAL_BYTES = 1024 * 1024;

const DATABASE_NAME = "ambient-widget-storage";
const DATABASE_VERSION = 1;
const ENTRY_STORE = "entries";
const WIDGET_INDEX = "by_widget";
const MAX_REQUEST_ID_LENGTH = 200;
const MAX_JSON_DEPTH = 32;
const MAX_JSON_NODES = 10_000;

type StorageOperation = "get" | "set" | "delete" | "clear" | "list";

export interface WidgetStorageRequest {
  type: "storage_request";
  request_id: unknown;
  operation: unknown;
  key?: unknown;
  value?: unknown;
  [key: string]: unknown;
}

export interface WidgetStorageError {
  code: string;
  message: string;
}

export interface WidgetStorageResponse {
  type: "storage_response";
  request_id: string;
  result?: unknown;
  error?: WidgetStorageError;
}

export interface WidgetStorageAdapter {
  get(widgetId: string, key: string): Promise<unknown | null>;
  usage(widgetId: string, key: string): Promise<WidgetStorageUsage>;
  set(widgetId: string, key: string, value: unknown): Promise<void>;
  delete(widgetId: string, key: string): Promise<void>;
  clear(widgetId: string): Promise<void>;
  list(widgetId: string): Promise<string[]>;
}

export interface WidgetStorageUsage {
  entries: number;
  totalBytes: number;
  existingBytes: number | null;
}

interface StoredWidgetValue {
  widgetId: string;
  key: string;
  value: unknown;
}

class StorageRequestError extends Error {
  readonly code: string;

  constructor(
    code: string,
    message: string,
  ) {
    super(message);
    this.code = code;
  }
}

const errorMessage = (error: unknown) =>
  String(error instanceof Error ? error.message : error || "Widget storage is unavailable")
    .slice(0, 1024);

const transactionError = (transaction: IDBTransaction) =>
  transaction.error ?? new Error("IndexedDB transaction failed");

const jsonBytes = (value: unknown) =>
  new TextEncoder().encode(JSON.stringify(value)).byteLength;

const plainJsonClone = (value: unknown): unknown => {
  const seen = new WeakSet<object>();
  let nodes = 0;

  const visit = (item: unknown, depth: number): void => {
    nodes += 1;
    if (nodes > MAX_JSON_NODES || depth > MAX_JSON_DEPTH) {
      throw new StorageRequestError(
        "storage_invalid_request",
        "Widget storage value is too complex",
      );
    }
    if (
      item === null
      || typeof item === "string"
      || typeof item === "boolean"
    ) {
      return;
    }
    if (typeof item === "number") {
      if (Number.isFinite(item)) return;
      throw new StorageRequestError(
        "storage_invalid_request",
        "Widget storage numbers must be finite",
      );
    }
    if (typeof item !== "object") {
      throw new StorageRequestError(
        "storage_invalid_request",
        "Widget storage accepts JSON values only",
      );
    }
    if (seen.has(item)) {
      throw new StorageRequestError(
        "storage_invalid_request",
        "Widget storage values cannot contain cycles",
      );
    }
    seen.add(item);
    if (Array.isArray(item)) {
      item.forEach((entry) => visit(entry, depth + 1));
    } else {
      const prototype = Object.getPrototypeOf(item);
      if (prototype !== Object.prototype && prototype !== null) {
        throw new StorageRequestError(
          "storage_invalid_request",
          "Widget storage accepts plain JSON objects only",
        );
      }
      if (Object.getOwnPropertySymbols(item).length > 0) {
        throw new StorageRequestError(
          "storage_invalid_request",
          "Widget storage accepts string object keys only",
        );
      }
      Object.values(item as Record<string, unknown>)
        .forEach((entry) => visit(entry, depth + 1));
    }
    seen.delete(item);
  };

  visit(value, 0);
  const serialized = JSON.stringify(value);
  if (
    serialized === undefined
    || new TextEncoder().encode(serialized).byteLength > MAX_WIDGET_STORAGE_VALUE_BYTES
  ) {
    throw new StorageRequestError(
      "storage_invalid_request",
      `Widget storage values are limited to ${MAX_WIDGET_STORAGE_VALUE_BYTES} bytes`,
    );
  }
  return JSON.parse(serialized) as unknown;
};

const storageKey = (value: unknown): string => {
  if (
    typeof value !== "string"
    || value.length === 0
    || value.length > MAX_WIDGET_STORAGE_KEY_LENGTH
    || new TextEncoder().encode(value).byteLength > MAX_WIDGET_STORAGE_KEY_LENGTH * 4
  ) {
    throw new StorageRequestError(
      "storage_invalid_request",
      `Widget storage keys must be 1-${MAX_WIDGET_STORAGE_KEY_LENGTH} characters`,
    );
  }
  return value;
};

const requestId = (value: unknown): string => {
  if (
    typeof value !== "string"
    || value.length === 0
    || value.length > MAX_REQUEST_ID_LENGTH
  ) {
    throw new StorageRequestError(
      "storage_invalid_request",
      "Widget storage request_id is invalid",
    );
  }
  return value;
};

const storageOperation = (value: unknown): StorageOperation => {
  if (
    value === "get"
    || value === "set"
    || value === "delete"
    || value === "clear"
    || value === "list"
  ) {
    return value;
  }
  throw new StorageRequestError(
    "storage_invalid_request",
    "Unsupported Widget storage operation",
  );
};


export class IndexedDbWidgetStorageAdapter implements WidgetStorageAdapter {
  private databasePromise: Promise<IDBDatabase> | null = null;
  private readonly factory: IDBFactory | undefined;

  constructor(factory: IDBFactory | undefined = globalThis.indexedDB) {
    this.factory = factory;
  }

  private database(): Promise<IDBDatabase> {
    if (this.databasePromise) return this.databasePromise;
    this.databasePromise = new Promise((resolve, reject) => {
      if (!this.factory) {
        reject(new Error("IndexedDB is unavailable"));
        return;
      }
      const request = this.factory.open(DATABASE_NAME, DATABASE_VERSION);
      request.onupgradeneeded = () => {
        const database = request.result;
        if (database.objectStoreNames.contains(ENTRY_STORE)) return;
        const store = database.createObjectStore(ENTRY_STORE, {
          keyPath: ["widgetId", "key"],
        });
        store.createIndex(WIDGET_INDEX, "widgetId", { unique: false });
      };
      request.onerror = () => reject(request.error ?? new Error("Failed to open IndexedDB"));
      request.onblocked = () => reject(new Error("Widget storage upgrade is blocked"));
      request.onsuccess = () => {
        const database = request.result;
        database.onversionchange = () => {
          database.close();
          this.databasePromise = null;
        };
        resolve(database);
      };
    });
    return this.databasePromise;
  }

  async get(widgetId: string, key: string): Promise<unknown | null> {
    const database = await this.database();
    return new Promise((resolve, reject) => {
      const transaction = database.transaction(ENTRY_STORE, "readonly");
      const request = transaction.objectStore(ENTRY_STORE).get([widgetId, key]);
      request.onerror = () => reject(request.error ?? transactionError(transaction));
      request.onsuccess = () => {
        const record = request.result as StoredWidgetValue | undefined;
        resolve(record ? record.value : null);
      };
    });
  }

  async usage(widgetId: string, key: string): Promise<WidgetStorageUsage> {
    const database = await this.database();
    return new Promise((resolve, reject) => {
      const transaction = database.transaction(ENTRY_STORE, "readonly");
      const request = transaction.objectStore(ENTRY_STORE)
        .index(WIDGET_INDEX)
        .getAll(widgetId);
      request.onerror = () => reject(request.error ?? transactionError(transaction));
      request.onsuccess = () => {
        const records = request.result as StoredWidgetValue[];
        let totalBytes = 0;
        let existingBytes: number | null = null;
        for (const record of records) {
          const bytes = jsonBytes(record.value);
          totalBytes += bytes;
          if (record.key === key) existingBytes = bytes;
        }
        resolve({
          entries: records.length,
          totalBytes,
          existingBytes,
        });
      };
    });
  }

  async set(widgetId: string, key: string, value: unknown): Promise<void> {
    const database = await this.database();
    return new Promise((resolve, reject) => {
      const transaction = database.transaction(ENTRY_STORE, "readwrite");
      transaction.onabort = () => reject(transactionError(transaction));
      transaction.onerror = () => reject(transactionError(transaction));
      transaction.oncomplete = () => resolve();
      transaction.objectStore(ENTRY_STORE).put({ widgetId, key, value });
    });
  }

  async delete(widgetId: string, key: string): Promise<void> {
    const database = await this.database();
    return new Promise((resolve, reject) => {
      const transaction = database.transaction(ENTRY_STORE, "readwrite");
      transaction.onabort = () => reject(transactionError(transaction));
      transaction.onerror = () => reject(transactionError(transaction));
      transaction.oncomplete = () => resolve();
      transaction.objectStore(ENTRY_STORE).delete([widgetId, key]);
    });
  }

  async clear(widgetId: string): Promise<void> {
    const database = await this.database();
    return new Promise((resolve, reject) => {
      const transaction = database.transaction(ENTRY_STORE, "readwrite");
      const store = transaction.objectStore(ENTRY_STORE);
      const request = store.index(WIDGET_INDEX).openCursor(widgetId);
      request.onsuccess = () => {
        const cursor = request.result;
        if (!cursor) return;
        cursor.delete();
        cursor.continue();
      };
      transaction.onabort = () => reject(transactionError(transaction));
      transaction.onerror = () => reject(transactionError(transaction));
      transaction.oncomplete = () => resolve();
    });
  }

  async list(widgetId: string): Promise<string[]> {
    const database = await this.database();
    return new Promise((resolve, reject) => {
      const transaction = database.transaction(ENTRY_STORE, "readonly");
      const request = transaction.objectStore(ENTRY_STORE)
        .index(WIDGET_INDEX)
        .getAllKeys(widgetId);
      request.onerror = () => reject(request.error ?? transactionError(transaction));
      request.onsuccess = () => {
        const keys = request.result
          .map((key) => Array.isArray(key) ? key[1] : undefined)
          .filter((key): key is string => typeof key === "string")
          .sort();
        resolve(keys);
      };
    });
  }
}

let defaultAdapter: IndexedDbWidgetStorageAdapter | null = null;

const browserStorageAdapter = () => {
  if (!defaultAdapter) defaultAdapter = new IndexedDbWidgetStorageAdapter();
  return defaultAdapter;
};


export class WidgetStorageBroker {
  private pending: Promise<void> = Promise.resolve();
  readonly widgetId: string;
  private readonly adapter: WidgetStorageAdapter;

  constructor(
    widgetId: string,
    adapter: WidgetStorageAdapter = browserStorageAdapter(),
  ) {
    this.widgetId = widgetId;
    this.adapter = adapter;
  }

  handle(message: WidgetStorageRequest): Promise<WidgetStorageResponse> {
    const response = this.pending.then(() => this.dispatch(message));
    this.pending = response.then(() => undefined, () => undefined);
    return response;
  }

  private async dispatch(message: WidgetStorageRequest): Promise<WidgetStorageResponse> {
    let id = "";
    try {
      id = requestId(message.request_id);
      const operation = storageOperation(message.operation);
      if (operation === "get") {
        return {
          type: "storage_response",
          request_id: id,
          result: await this.adapter.get(this.widgetId, storageKey(message.key)),
        };
      }
      if (operation === "set") {
        const key = storageKey(message.key);
        const value = plainJsonClone(message.value);
        const valueBytes = jsonBytes(value);
        const usage = await this.adapter.usage(this.widgetId, key);
        const nextEntries = usage.entries + (usage.existingBytes === null ? 1 : 0);
        const nextTotalBytes =
          usage.totalBytes - (usage.existingBytes ?? 0) + valueBytes;
        if (
          nextEntries > MAX_WIDGET_STORAGE_ENTRIES
          || nextTotalBytes > MAX_WIDGET_STORAGE_TOTAL_BYTES
        ) {
          throw new StorageRequestError(
            "storage_quota_exceeded",
            `Widget storage is limited to ${MAX_WIDGET_STORAGE_ENTRIES} entries and ${MAX_WIDGET_STORAGE_TOTAL_BYTES} bytes`,
          );
        }
        await this.adapter.set(this.widgetId, key, value);
        return {
          type: "storage_response",
          request_id: id,
          result: { status: "ok" },
        };
      }
      if (operation === "delete") {
        await this.adapter.delete(this.widgetId, storageKey(message.key));
        return {
          type: "storage_response",
          request_id: id,
          result: { status: "ok" },
        };
      }
      if (operation === "clear") {
        await this.adapter.clear(this.widgetId);
        return {
          type: "storage_response",
          request_id: id,
          result: { status: "ok" },
        };
      }
      return {
        type: "storage_response",
        request_id: id,
        result: (await this.adapter.list(this.widgetId)).sort(),
      };
    } catch (error) {
      return {
        type: "storage_response",
        request_id: id,
        error: {
          code: error instanceof StorageRequestError
            ? error.code
            : error instanceof DOMException && error.name === "QuotaExceededError"
              ? "storage_quota_exceeded"
              : "storage_unavailable",
          message: errorMessage(error),
        },
      };
    }
  }
}
