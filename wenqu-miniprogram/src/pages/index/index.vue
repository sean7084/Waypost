<template>
  <view class="page">
    <view class="card">
      <text class="title">Helpdesk Mini</text>
      
      <!-- Loading State -->
      <view v-if="loading" class="state-box">
        <text class="sub">正在登录...</text>
      </view>

      <view v-else-if="navigating" class="state-box">
        <text class="sub">验证成功，正在进入聊天...</text>
      </view>

      <!-- New User / Authorization Button -->
      <view v-else-if="isNewUser" class="state-box">
        <text class="sub">欢迎使用！请授权以继续。</text>
        <text v-if="statusError" class="error-text">{{ statusError }}</text>
        <button class="btn" @tap="handleAuthorize">授权登录</button>
      </view>

      <view v-else class="state-box">
        <text class="sub">{{ loginPrompt }}</text>
        <text v-if="statusError" class="error-text">{{ statusError }}</text>
        <button class="btn" @tap="doLogin">微信登录</button>
      </view>
    </view>
  </view>
</template>

<script>
import { wxLogin, updateWxUser } from '../../utils/api.js'

export default {
  data() {
    return {
      loading: true,
      navigating: false,
      isNewUser: false,
      loginPrompt: '请登录以继续。',
      statusError: '',
      externalUserId: ''
    }
  },
  onLoad(options) {
    if (options && options.loggedOut === '1') {
      this.loading = false
      this.navigating = false
      this.loginPrompt = '已退出登录，请重新登录。'
      this.statusError = ''
      return
    }
    this.doLogin()
  },
  methods: {
    resolveStatusError(error, fallback = '登录失败，请重试。') {
      const payload = error && typeof error === 'object' ? error.data : null
      if (payload && Array.isArray(payload.detail) && payload.detail.length) {
        const firstDetail = payload.detail[0] || {}
        if (firstDetail.msg) {
          return String(firstDetail.msg)
        }
        if (firstDetail.message) {
          return String(firstDetail.message)
        }
      }

      if (payload && typeof payload.detail === 'string' && payload.detail.trim()) {
        return payload.detail.trim()
      }

      if (payload && typeof payload.message === 'string' && payload.message.trim()) {
        return payload.message.trim()
      }

      if (error && typeof error.errMsg === 'string' && error.errMsg.trim()) {
        return error.errMsg.trim()
      }

      if (error && typeof error.message === 'string' && error.message.trim()) {
        return error.message.trim()
      }

      const statusCode = Number(error && error.statusCode)
      if (statusCode) {
        return `请求失败 (${statusCode})`
      }

      return fallback
    },
    async doLogin() {
      this.loading = true
      this.navigating = false
      this.isNewUser = false
      this.statusError = ''
      try {
        const loginRes = await new Promise((resolve, reject) => {
          uni.login({
            provider: 'weixin',
            success: resolve,
            fail: reject
          })
        })

        if (!loginRes.code) throw new Error('微信登录未返回有效 code')

        const res = await wxLogin(loginRes.code)
        
        if (res.status === 'new_user') {
          this.storeSession(res)
          this.isNewUser = true
        } else {
          await this.finishLogin(res)
        }
      } catch (e) {
        console.error(e)
        this.navigating = false
        this.loginPrompt = '登录失败，请重新登录。'
        this.statusError = this.resolveStatusError(e)
      } finally {
        if (!this.navigating) {
          this.loading = false
        }
      }
    },

    async handleAuthorize() {
      try {
        const profileRes = await new Promise((resolve, reject) => {
          uni.getUserProfile({
            desc: '用于完善会员资料',
            success: resolve,
            fail: reject
          })
        })

        const res = await updateWxUser({
          userInfo: profileRes.userInfo
        })

        await this.finishLogin(res)
      } catch (e) {
        console.error(e)
        this.statusError = this.resolveStatusError(e, '授权失败，请重试。')
      }
    },

    storeSession(data) {
      if (data.auth_token) uni.setStorageSync('auth_token', data.auth_token)

      const user = data.user
      if (user) {
        if (user.user_id) uni.setStorageSync('user_id', user.user_id)
        if (user.external_id) {
          uni.setStorageSync('external_id', user.external_id)
          uni.setStorageSync('external_user_id', user.external_id)
        }
        if (user.nickname) uni.setStorageSync('nickname', user.nickname)
        if (user.avatar) uni.setStorageSync('avatar', user.avatar)
      }
    },

    finishLogin(data) {
      this.storeSession(data)
      this.loading = false
      this.navigating = true
      return new Promise((resolve, reject) => {
        uni.navigateTo({
          url: '/pages/chat/chat',
          success: resolve,
          fail: reject
        })
      })
    }
  }
}
</script>

<style scoped>
.page {
  height: 100vh;
  background: #f5f5f5;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40rpx;
}
.card {
  width: 100%;
  background: #ffffff;
  padding: 48rpx 32rpx;
  border-radius: 18rpx;
  box-shadow: 0 4rpx 20rpx rgba(0,0,0,0.08);
  text-align: center;
}
.state-box {
  margin-top: 40rpx;
}
.title {
  display: block;
  font-size: 40rpx;
  font-weight: 700;
  margin-bottom: 20rpx;
}
.sub {
  display: block;
  font-size: 28rpx;
  color: #666666;
  margin-bottom: 30rpx;
}
.error-text {
  display: block;
  margin-bottom: 24rpx;
  font-size: 24rpx;
  line-height: 1.6;
  color: #dc2626;
  word-break: break-word;
}
.btn {
  background: #07c160;
  color: #ffffff;
  border-radius: 14rpx;
  font-size: 32rpx;
  height: 88rpx;
  line-height: 88rpx;
}
</style>

