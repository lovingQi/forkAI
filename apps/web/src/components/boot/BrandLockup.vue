<template>
  <span class="brand-lockup">
    <img :src="logoUrl" alt="" />
  </span>
</template>

<script setup lang="ts">
import logoUrl from '@/assets/boot/logo.png'
</script>

<style scoped>
.brand-lockup {
  display: flex;
  width: 100%;
  height: 100%;
  align-items: center;
  justify-content: center;
}
.brand-lockup img {
  max-width: 100%;
  max-height: 100%;
  width: auto;
  height: auto;
  object-fit: contain;
  display: block;
}
</style>
