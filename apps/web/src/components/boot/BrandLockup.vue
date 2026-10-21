<template>
  <span class="brand-lockup" v-html="svg" />
</template>

<script setup lang="ts">
import { lockupSvg } from './lockup'

const svg = lockupSvg()
</script>

<style scoped>
.brand-lockup {
  display: block;
  line-height: 0;
}
.brand-lockup :deep(svg) {
  width: 100%;
  height: auto;
  display: block;
}
</style>
