/* 右下角浮层提醒（破档 / 补仓 / 日结定格） */
window.ToastStack = {
  props: ["items"],
  template: `
    <div class="toasts">
      <div class="toast" v-for="t in items" :key="t.id" :class="t.kind">
        <div class="t">{{ t.title }}</div>
        <div class="m">{{ t.message }}</div>
      </div>
    </div>`,
};
