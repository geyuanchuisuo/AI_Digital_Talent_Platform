/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // antd 5 的 ESM 产物在 App Router 的 RSC 编译链里需要显式转译（官方 Next.js 示例同款）
  transpilePackages: ['antd', '@ant-design/icons', '@ant-design/cssinjs', 'rc-util', 'rc-pagination', 'rc-picker'],
};

export default nextConfig;
