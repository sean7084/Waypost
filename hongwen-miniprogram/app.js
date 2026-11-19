// App entry for 泓问.
//
// Login is driven by pages/index (wx.login -> /auth/wx_login); the app object
// only restores the stored profile so any page can read getApp().globalData
// without touching storage itself. Token refresh / expiry is handled centrally
// by utils/request.js.
const auth = require('./utils/auth');

App({
  onLaunch() {
    this.globalData.userInfo = auth.getStoredUser();
    this.globalData.hasPhone = auth.hasPhone();
  },

  // Called by pages/index after a successful login or profile authorization.
  syncSession() {
    this.globalData.userInfo = auth.getStoredUser();
    this.globalData.hasPhone = auth.hasPhone();
  },

  globalData: {
    userInfo: null,
    hasPhone: false,
  },
});