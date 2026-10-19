// Device verification: category-driven capture fields + photos, saved to the
// offline queue (idempotent per client_device_uid) for later sync.
// Blind-count capture: PC readings render as dropdowns (with an "其他/Other"
// free-text escape), required readings are enforced before save, iOS company
// phones require a user email, and SN / Asset ID accept 1D barcode scans.
const db = require('../../utils/db');
const queue = require('../../utils/queue');
const request = require('../../utils/request');
const labels = require('../../utils/labels');
const image = require('../../utils/image');

const OTHER = '其他';
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const PHOTO_FIELDS = ['overall_photo', 'serial_photo'];

Page({
  data: {
    id: '',
    uid: '',
    isNew: false,
    device: {},
    fields: {},
    captureFields: [],
    requiredFlags: {},
    fieldLabels: labels.FIELD_LABELS,
    pickerRanges: {},
    pickerIndex: {},
    otherMode: {},
    overallPhoto: '',
    serialPhoto: '',
    status: 'in_store',
  },

  onLoad(query) {
    this.init(query);
  },

  async init(query) {
    const id = query.id;
    const cached = db.getInspection(id);
    // Pickers + required readings come from the checklist; fetch it when the
    // offline cache is empty so connected engineers get the structured form too.
    let checklist = db.getChecklist();
    if (!checklist) {
      try {
        checklist = await request.request('/inspections/checklist/');
        db.setChecklist(checklist);
      } catch (err) {
        checklist = {};
      }
    }
    checklist = checklist || {};
    const captureMap = checklist.capture_fields || {};
    const optionsMap = checklist.field_options || {};
    const requiredMap = checklist.required_fields || {};
    let device = {};
    let uid = query.uid || '';
    const isNew = query.new === '1';

    if (isNew) {
      uid = uid || `new-${queue.uid()}`;
      device = {
        client_device_uid: uid,
        sn: query.sn ? decodeURIComponent(query.sn) : '',
        is_new_device: true,
        category: '',
        brand_model: '',
        status: 'in_store',
      };
    } else if (cached) {
      device = (cached.devices || []).find((d) => d.client_device_uid === query.uid) || {};
      uid = device.client_device_uid || uid;
    }

    const category = device.category || '';
    const captureFields =
      captureMap[category] ||
      checklist.default_capture_fields ||
      ['intact_asset_tag', 'comment', 'overall_photo', 'serial_photo'];
    const requiredFields = requiredMap[category] || checklist.default_required_fields || [];
    const requiredFlags = {};
    requiredFields.forEach((f) => {
      requiredFlags[f] = true;
    });

    const fields = {};
    captureFields.forEach((f) => {
      if (PHOTO_FIELDS.indexOf(f) > -1) return;
      fields[f] = f === 'is_company_phone' ? !!device[f] : device[f] || '';
    });

    // Dropdown pickers: range = configured options + an "Other" escape hatch.
    const pickerRanges = {};
    const pickerIndex = {};
    const otherMode = {};
    captureFields.forEach((f) => {
      const opts = optionsMap[f];
      if (!opts) return;
      const range = opts.concat([OTHER]);
      pickerRanges[f] = range;
      const cur = fields[f];
      let idx = cur ? range.indexOf(cur) : -1;
      if (cur && idx === -1) {
        otherMode[f] = true;
        idx = range.length - 1;
      }
      pickerIndex[f] = idx >= 0 ? idx : 0;
    });

    this.setData({
      id,
      uid,
      isNew,
      device,
      fields,
      captureFields,
      requiredFlags,
      pickerRanges,
      pickerIndex,
      otherMode,
      status: device.status || 'in_store',
    });
  },

  onField(e) {
    const key = e.currentTarget.dataset.field;
    this.setData({ [`fields.${key}`]: e.detail.value });
  },

  onIdentity(e) {
    const key = e.currentTarget.dataset.field;
    this.setData({ [`device.${key}`]: e.detail.value });
  },

  // 1D barcode scan into SN / Asset ID.
  scanField(e) {
    const key = e.currentTarget.dataset.field;
    wx.scanCode({
      scanType: ['barCode', 'qrCode'],
      success: (res) => {
        const code = (res.result || '').trim();
        if (code) this.setData({ [`device.${key}`]: code });
      },
    });
  },

  onPickerChange(e) {
    const f = e.currentTarget.dataset.field;
    const range = this.data.pickerRanges[f] || [];
    const idx = Number(e.detail.value);
    const val = range[idx];
    if (val === OTHER) {
      this.setData({ [`otherMode.${f}`]: true, [`fields.${f}`]: '', [`pickerIndex.${f}`]: idx });
    } else {
      this.setData({ [`otherMode.${f}`]: false, [`fields.${f}`]: val, [`pickerIndex.${f}`]: idx });
    }
  },

  onCompanyPhone(e) {
    this.setData({ 'fields.is_company_phone': e.detail.value });
  },

  onStatus(e) {
    this.setData({ status: e.currentTarget.dataset.status });
  },

  async choosePhoto(e) {
    const part = e.currentTarget.dataset.part; // overall_photo | serial_photo
    try {
      const [saved] = await image.pickPhotos({ count: 1 });
      this.setData({ [part]: saved });
    } catch (_e) {
      // user cancelled
    }
  },

  onSave() {
    const { fields, requiredFlags, fieldLabels } = this.data;
    const missing = [];
    Object.keys(requiredFlags).forEach((f) => {
      const v = fields[f];
      if (v === undefined || v === null || String(v).trim() === '') {
        missing.push(fieldLabels[f] || f);
      }
    });
    if (fields.is_company_phone && !EMAIL_RE.test(String(fields.user_email || '').trim())) {
      missing.push(fieldLabels.user_email || 'User (Email)');
    }
    if (missing.length) {
      wx.showToast({ title: `请填写：${missing.join('、')}`, icon: 'none' });
      return;
    }

    // A device marked not-found during the inspection requires a mandatory note.
    if (this.data.status === 'not_in_store' && !(this.data.fields.comment || '').trim()) {
      wx.showToast({ title: '未找到设备时必须填写备注', icon: 'none' });
      return;
    }

    const photos = [];
    if (this.data.overallPhoto) {
      photos.push({ part: 'overall_photo', kind: 'overall', filePath: this.data.overallPhoto, uid: queue.uid(), index: 1 });
    }
    if (this.data.serialPhoto) {
      photos.push({ part: 'serial_photo', kind: 'serial', filePath: this.data.serialPhoto, uid: queue.uid(), index: 2 });
    }

    const out = { ...this.data.fields, status: this.data.status };
    if (this.data.isNew) {
      out.category = this.data.device.category || '';
      out.brand_model = this.data.device.brand_model || '';
      out.sn = this.data.device.sn || '';
      out.asset_id_text = this.data.device.asset_id_text || '';
      out.usage = this.data.device.usage || '';
      out.is_new_device = true;
    }

    queue.enqueue({
      type: 'device',
      inspectionId: this.data.id,
      client_device_uid: this.data.uid,
      dedupeKey: `device:${this.data.id}:${this.data.uid}`,
      fields: out,
      photos,
    });

    wx.showToast({ title: '已保存，待同步', icon: 'success' });
    setTimeout(() => wx.navigateBack(), 600);
  },
});
