"use strict";
// Only unsigned opinions are buffered, never passwords, tokens or textbook pages.
window.ReviewVault = (() => {
  let opened;
  const keys = new Map();
  function database() {
    if (!opened)
      opened = new Promise((resolve, reject) => {
        const request = indexedDB.open("ustc-private-review-opinions", 1);
        request.onupgradeneeded = () =>
          request.result.createObjectStore("vault");
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
      });
    return opened;
  }
  async function read(id) {
    const db = await database();
    return new Promise((resolve, reject) => {
      const r = db.transaction("vault").objectStore("vault").get(id);
      r.onsuccess = () => resolve(r.result);
      r.onerror = () => reject(r.error);
    });
  }
  async function deviceKey(user) {
    if (!keys.has(user))
      keys.set(
        user,
        (async () => {
          const generated = await crypto.subtle.generateKey(
            { name: "AES-GCM", length: 256 },
            false,
            ["encrypt", "decrypt"],
          );
          const db = await database();
          return new Promise((resolve, reject) => {
            const tx = db.transaction("vault", "readwrite"),
              store = tx.objectStore("vault");
            let selected;
            const request = store.get("key:" + user);
            request.onsuccess = () => {
              selected = request.result || generated;
              if (!request.result) store.put(generated, "key:" + user);
            };
            tx.oncomplete = () => resolve(selected);
            tx.onerror = () => reject(tx.error);
          });
        })(),
      );
    return keys.get(user);
  }
  const id = (user, key) => "draft:" + user + ":" + key;
  async function put(user, key, value) {
    const nonce = crypto.getRandomValues(new Uint8Array(12));
    const cipher = await crypto.subtle.encrypt(
      { name: "AES-GCM", iv: nonce },
      await deviceKey(user),
      new TextEncoder().encode(JSON.stringify(value)),
    );
    const db = await database();
    await new Promise((resolve, reject) => {
      const tx = db.transaction("vault", "readwrite");
      tx.objectStore("vault").put(
        { nonce, cipher, tag: value.mutation_id },
        id(user, key),
      );
      tx.oncomplete = resolve;
      tx.onerror = () => reject(tx.error);
    });
  }
  async function get(user, key) {
    const value = await read(id(user, key));
    if (!value) return null;
    const plain = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: value.nonce },
      await deviceKey(user),
      value.cipher,
    );
    return JSON.parse(new TextDecoder().decode(plain));
  }
  async function remove(user, key, tag) {
    const db = await database();
    return new Promise((resolve, reject) => {
      const tx = db.transaction("vault", "readwrite"),
        store = tx.objectStore("vault"),
        r = store.get(id(user, key));
      r.onsuccess = () => {
        if (r.result && r.result.tag === tag) store.delete(id(user, key));
      };
      tx.oncomplete = resolve;
      tx.onerror = () => reject(tx.error);
    });
  }
  async function pending(user) {
    const db = await database();
    return new Promise((resolve, reject) => {
      const r = db.transaction("vault").objectStore("vault").getAllKeys();
      r.onsuccess = () =>
        resolve(
          r.result
            .filter(
              (k) =>
                typeof k === "string" && k.startsWith("draft:" + user + ":"),
            )
            .map((k) => k.slice(("draft:" + user + ":").length)),
        );
      r.onerror = () => reject(r.error);
    });
  }
  return { put, get, remove, pending };
})();
