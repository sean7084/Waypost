// Item 9 - image upload: mock chooseMedia (album) + real compress/save through
// utils/image.pickPhotos + real wx.uploadFile to /files/upload, assert
// pendingMediaFileId and <=15MB, then submit it with text as a mixed turn.
// Album/camera popups are MANUAL.
const lib = require('./lib');
const fs = require('fs');
const path = require('path');

module.exports = {
  id: 9,
  key: '09-image',
  title: '图片上传（压缩 <=15MB / pending / 随文字 mixed 提交）',
  mode: 'mock',
  async run(mini) {
    const evidence = [];
    const notes = [];
    const chat = await lib.gotoChat(mini);
    await lib.linkCustomer(mini);
    await lib.callPageMethod(mini, 'handleCreateNewSession');
    await lib.waitForData(chat, (d) => Boolean(d.currentSessionId) && !d.sending, { timeout: 15000, label: 'fresh session' });

    // Generate a normal-content test JPEG and write it into the devtools filesystem.
    const imgPath = path.join(lib.ASSET_DIR, 'test-image.jpg');
    lib.fixture('make-image', imgPath);
    const b64 = fs.readFileSync(imgPath).toString('base64');
    const local = await mini.evaluate((data) => {
      const fsm = wx.getFileSystemManager();
      const p = `${wx.env.USER_DATA_PATH}/automator-test.jpg`;
      fsm.writeFileSync(p, data, 'base64');
      return { path: p, size: fsm.statSync(p).size };
    }, b64);
    lib.assert(local.size > 0 && local.size <= 15 * 1024 * 1024, `source image within 15MB cap (${local.size} bytes)`);

    // Mock chooseMedia (album); compression/save run for real via utils/image.
    await mini.mockWxMethod(
      'chooseMedia',
      `function(){ return { tempFiles: [{ tempFilePath: ${JSON.stringify(local.path)}, size: ${local.size}, fileType: 'image' }], errMsg: 'chooseMedia:ok' }; }`
    );
    const picked = await mini.evaluate(async () => {
      const image = require('utils/image');
      const paths = await image.pickPhotos({ count: 1, sourceType: ['album'] });
      const fsm = wx.getFileSystemManager();
      const p = paths[0];
      return { path: p, size: fsm.statSync(p).size };
    });
    await mini.restoreWxMethod('chooseMedia');
    lib.assert(picked.path, 'pickPhotos returned a compressed/saved path');
    lib.assert(picked.size > 0 && picked.size <= 15 * 1024 * 1024, `compressed image <=15MB (${picked.size} bytes)`);
    notes.push(`chooseMedia(album mock) -> real compressImage/saveFile -> ${picked.path} (${picked.size} bytes <=15MB)`);

    // Real uploadFile through the page handler -> pendingMediaFileId.
    await lib.callPageMethod(mini, 'onSendMedia', { detail: { filePath: picked.path, previewPath: picked.path, textDraft: '' } });
    await lib.waitForData(chat, (d) => Boolean(d.pendingMediaFileId), { timeout: 60000, label: 'upload -> pendingMediaFileId' });
    const pendingId = await chat.data('pendingMediaFileId');
    const pendingType = await chat.data('pendingMediaType');
    lib.assert(pendingType === 'image', `pendingMediaType=image (got ${pendingType})`);
    evidence.push(await lib.shot(mini, '09-pending-media'));
    notes.push(`upload: real wx.uploadFile /files/upload -> file_id=${pendingId}, type=image`);

    // Submit the pending image together with text as a mixed turn.
    const mixed = lib.firePageMethod(mini, 'onSendText', { detail: { text: '请看看这张图片，描述一下你看到的内容' } });
    await lib.waitFor(
      async () => ((await chat.data('sending')) === false ? true : null),
      { timeout: 150000, interval: 300, label: 'mixed turn complete' }
    );
    await mixed.catch(() => {});
    const cleared = await chat.data('pendingMediaFileId');
    lib.assert(cleared === '', 'pendingMediaFileId cleared after mixed submit');
    const msgs = await chat.data('messages');
    lib.assert(msgs.some((m) => m.role === 'user' && m.type === 'image'), 'user image message present');
    const lastAssistant = [...msgs].reverse().find((m) => m.role === 'assistant' && m.type === 'text');
    lib.assert(lastAssistant && String(lastAssistant.content || '').length > 0, 'assistant replied to the mixed turn');
    evidence.push(await lib.shot(mini, '09-mixed-submitted'));
    notes.push(`mixed submit: text + image_file_ids=[${pendingId}] -> type mixed SSE reply (${String(lastAssistant.content).length} chars), pending cleared`);
    notes.push('MANUAL(真机): 相册/拍照两条真实入口与系统压缩需真机扫码验证。');

    return { status: 'PASS', evidence, notes, manual: ['真机相册/拍照入口 + 系统压缩 <=15MB'] };
  },
};