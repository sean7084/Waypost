// Rack / network / speedtest / issue photos. Each captured photo is persisted
// locally and queued (type 'photo') for offline sync to /inspections/{id}/photos/.
const queue = require('../../utils/queue');
const image = require('../../utils/image');

const KINDS = [
  { kind: 'rack1', label: '机柜 Rack 1' },
  { kind: 'rack2', label: '机柜 Rack 2' },
  { kind: 'rack3', label: '机柜 Rack 3' },
  { kind: 'router', label: '路由器 Router' },
  { kind: 'switch', label: '交换机 Switch' },
  { kind: 'patchpanel', label: '配线架 Patch Panel' },
  { kind: 'speedtest_ethernet', label: '有线网速 Speedtest (Ethernet)' },
  { kind: 'speedtest_wifi', label: '无线网速 Speedtest (WiFi)' },
  { kind: 'issue', label: '问题照片 Issue' },
];

Page({
  data: { id: '', kinds: KINDS, shots: {}, wifiMode: 'good', weakPoints: [] },

  onLoad(query) {
    this.setData({ id: query.id, shots: {}, wifiMode: 'good', weakPoints: [] });
  },

  setWifiGood() {
    this.setData({ wifiMode: 'good', weakPoints: [] });
  },

  setWifiWeak() {
    const weakPoints = this.data.weakPoints.length ? this.data.weakPoints : [{ location: '', description: '' }];
    this.setData({ wifiMode: 'weak', weakPoints });
  },

  addWeak() {
    this.setData({ weakPoints: this.data.weakPoints.concat([{ location: '', description: '' }]) });
  },

  removeWeak(e) {
    const index = e.currentTarget.dataset.index;
    this.setData({ weakPoints: this.data.weakPoints.filter((_, i) => i !== index) });
  },

  weakInput(e) {
    const { index, field } = e.currentTarget.dataset;
    const weakPoints = this.data.weakPoints.slice();
    weakPoints[index] = { ...weakPoints[index], [field]: e.detail.value };
    this.setData({ weakPoints });
  },

  async choose(e) {
    const kind = e.currentTarget.dataset.kind;
    try {
      const saved = await image.pickPhotos({ count: 9 });
      const shots = { ...this.data.shots };
      const existing = shots[kind] || [];
      shots[kind] = existing.concat(saved.map((path) => ({ path, uid: queue.uid() })));
      this.setData({ shots });
    } catch (_e) {
      // user cancelled
    }
  },

  removeShot(e) {
    const { kind, index } = e.currentTarget.dataset;
    const shots = { ...this.data.shots };
    shots[kind] = (shots[kind] || []).filter((_, i) => i !== index);
    this.setData({ shots });
  },

  save() {
    let count = 0;
    Object.keys(this.data.shots).forEach((kind) => {
      (this.data.shots[kind] || []).forEach((shot, index) => {
        queue.enqueue({
          type: 'photo',
          inspectionId: this.data.id,
          kind,
          filePath: shot.path,
          client_photo_uid: shot.uid,
          sort_index: index + 1,
          dedupeKey: `photo:${this.data.id}:${shot.uid}`,
        });
        count += 1;
      });
    });
    // WiFi coverage: good = empty weak-point list; weak = the captured points.
    queue.enqueue({
      type: 'wifi_weak_points',
      inspectionId: this.data.id,
      weakPoints: this.data.wifiMode === 'weak' ? this.data.weakPoints : [],
      weak_points: this.data.wifiMode === 'weak' ? this.data.weakPoints : [],
      dedupeKey: `wifi:${this.data.id}`,
    });
    count += 1;
    this.setData({ shots: {} });
    wx.showToast({ title: `已加入 ${count} 项，待同步`, icon: 'success' });
    setTimeout(() => wx.navigateBack(), 700);
  },
});
