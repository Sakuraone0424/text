"use strict";
window.ReviewOpinion = class {
  constructor(user, packet, api, root) {
    this.user = user;
    this.packet = packet;
    this.api = api;
    this.root = root;
    this.note = root.querySelector("#note");
    this.boxes = [...root.querySelectorAll("[data-check]")];
    this.status = root.querySelector("#note-status");
    this.conflictRoot = root.querySelector("#note-conflict");
    this.version = 0;
    this.dirty = false;
    this.conflict = false;
    this.serial = 0;
    this.pipeline = Promise.resolve();
    this.localPipeline = Promise.resolve();
  }
  values() {
    return {
      note: this.note.value,
      checks: Object.fromEntries(
        this.boxes.map((e) => [e.dataset.check, e.checked]),
      ),
    };
  }
  same(a, b) {
    return (
      a.note === b.note &&
      this.boxes.every(
        (e) => a.checks[e.dataset.check] === b.checks[e.dataset.check],
      )
    );
  }
  apply(value) {
    this.note.value = value.note || "";
    this.boxes.forEach((e) => (e.checked = !!value.checks[e.dataset.check]));
  }
  say(value) {
    this.status.textContent = value;
  }
  async start() {
    try {
      this.remote = await this.api(
        "/api/candidates/" +
          encodeURIComponent(this.packet.candidate_key) +
          "/note",
      );
    } catch (e) {
      this.remote = { version: 0, note: "", checks: {}, state: "unknown" };
      this.say("暂时无法读取服务器草稿，意见将先在本机加密暂存。");
    }
    this.version = this.remote.version;
    if (this.remote.state === "editing") this.apply(this.remote);
    let local = null;
    try {
      local = await ReviewVault.get(this.user, this.packet.candidate_key);
    } catch (e) {
      this.say(
        "此浏览器的加密暂存不可用，请保持联网并确认服务器保存成功后退出。",
      );
    }
    if (local && local.revision_sha256 === this.packet.revision_sha256) {
      if (
        this.remote.state === "submitted" &&
        this.packet.decisions.some(
          (d) =>
            d.reviewer === this.user &&
            d.note === local.note.trim() &&
            this.same({ note: local.note, checks: d.checks }, local),
        )
      ) {
        await ReviewVault.remove(
          this.user,
          this.packet.candidate_key,
          local.mutation_id,
        );
      } else if (
        this.same(local, this.remote) &&
        this.remote.state === "editing"
      ) {
        await ReviewVault.remove(
          this.user,
          this.packet.candidate_key,
          local.mutation_id,
        );
        this.say("已恢复服务器草稿，尚未签署。");
      } else {
        this.apply(local);
        this.dirty = true;
        this.mutation = local.mutation_id;
        if (
          local.note_version !== this.version ||
          local.base_decision_id !== this.packet.last_decision_id
        ) {
          this.conflict = true;
          this.showConflict();
        } else this.say("已恢复本机加密草稿，等待同步；本人声明未勾选。");
      }
    } else if (this.remote.state === "editing" && this.remote.version)
      this.say("已恢复服务器草稿；暂存不等于人工签署。");
    const input = () => {
      this.serial++;
      this.dirty = true;
      this.persist().catch(() =>
        this.say("本机暂存失败，请勿退出，先完成服务器同步。"),
      );
      clearTimeout(this.timer);
      this.timer = setTimeout(() => this.flush().catch(() => {}), 600);
    };
    this.note.addEventListener("input", input);
    this.boxes.forEach((e) => e.addEventListener("change", input));
    if (this.dirty && !this.conflict) this.flush().catch(() => {});
  }
  persist() {
    const payload = {
      ...this.values(),
      revision_sha256: this.packet.revision_sha256,
      note_version: this.version,
      base_decision_id: this.packet.last_decision_id,
      mutation_id: crypto.randomUUID(),
    };
    this.mutation = payload.mutation_id;
    this.localPipeline = this.localPipeline
      .catch(() => {})
      .then(() =>
        ReviewVault.put(this.user, this.packet.candidate_key, payload),
      )
      .then(() => {
        if (this.dirty) this.say("已在本机加密暂存，待服务器同步；尚未签署。");
        window.dispatchEvent(new Event("review-opinion-buffer"));
      });
    return this.localPipeline;
  }
  flush() {
    clearTimeout(this.timer);
    this.pipeline = this.pipeline
      .catch(() => {})
      .then(async () => {
        if (!this.dirty) return;
        if (this.conflict)
          throw new Error("请先比较本机和服务器意见，系统没有覆盖任何一方。");
        const serial = this.serial,
          values = this.values();
        try {
          await this.persist();
        } catch (e) {
          this.say("本机加密暂存不可用，正在尝试服务器保存。");
        }
        const mutation = this.mutation;
        try {
          const result = await this.api(
            "/api/candidates/" +
              encodeURIComponent(this.packet.candidate_key) +
              "/note",
            {
              method: "POST",
              keepalive: true,
              body: JSON.stringify({
                ...values,
                revision_sha256: this.packet.revision_sha256,
                expected_version: this.version,
                expected_last_decision_id: this.packet.last_decision_id,
              }),
            },
          );
          this.version = result.version;
          this.remote = result;
          if (this.serial === serial) {
            this.dirty = false;
            await ReviewVault.remove(
              this.user,
              this.packet.candidate_key,
              mutation,
            ).catch(() => {});
            this.say(
              "已保存到服务器（草稿版本 " + this.version + "），尚未人工签署。",
            );
            window.dispatchEvent(new Event("review-opinion-buffer"));
          }
        } catch (e) {
          this.say("未同步，意见保留在本机加密草稿。" + e.message);
          if (e.status === 409) {
            this.conflict = true;
            this.remote = await this.api(
              "/api/candidates/" +
                encodeURIComponent(this.packet.candidate_key) +
                "/note",
            );
            this.latestPacket = await this.api(
              "/api/candidates/" +
                encodeURIComponent(this.packet.candidate_key),
            );
            this.showConflict();
          }
          throw e;
        }
      });
    return this.pipeline;
  }
  showConflict() {
    const root = this.conflictRoot;
    root.replaceChildren();
    root.hidden = false;
    const p = document.createElement("p");
    p.textContent =
      "检测到另一设备或审核决定更新。没有覆盖任一份意见，请比较后选择。";
    root.append(p);
    const pre = document.createElement("pre");
    pre.textContent = "服务器草稿：\n" + this.remote.note;
    root.append(pre);
    for (const [useLocal, label] of [
      [false, "使用服务器草稿"],
      [true, "已比较，保留本机意见作为新草稿"],
    ]) {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = label;
      b.onclick = () => {
        if (useLocal && this.latestPacket?.superseded_by) {
          this.say("该题已有新版本，旧意见已保留，请打开新版后重新判断。");
          return;
        }
        if (this.latestPacket)
          this.packet.last_decision_id = this.latestPacket.last_decision_id;
        this.version = this.remote.version;
        this.conflict = false;
        root.hidden = true;
        if (!useLocal) {
          this.apply(
            this.remote.state === "editing"
              ? this.remote
              : { note: "", checks: {} },
          );
          this.dirty = false;
          ReviewVault.remove(
            this.user,
            this.packet.candidate_key,
            this.mutation,
          ).catch(() => {});
          this.say("已载入服务器草稿，未提交签署。");
        } else {
          this.serial++;
          this.dirty = true;
          this.flush().catch(() => {});
        }
      };
      root.append(b);
    }
  }
};
