export class SerialTaskQueue {
  #tail = Promise.resolve();

  enqueue(task) {
    if (typeof task !== "function") {
      throw new TypeError("SerialTaskQueue task must be a function");
    }
    const operation = this.#tail.then(() => task());
    this.#tail = operation.catch(() => undefined);
    return operation;
  }
}
