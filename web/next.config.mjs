/** 공개 주소 하위 경로. 빌드 시 NEXT_PUBLIC_BASE_PATH로 바꿀 수 있다(기본 /gembti). */
const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "/gembti";
const isProd = process.env.NODE_ENV === "production";

/** @type {import('next').NextConfig} */
const nextConfig = {
  basePath,
  trailingSlash: true,
  images: { unoptimized: true },
  poweredByHeader: false,
  env: { NEXT_PUBLIC_BASE_PATH: basePath },
  ...(isProd
    ? { output: "export" }
    : {
        // 개발 중에만: 브라우저는 같은 출처의 /gembti/api 를 부르고 Next가 로컬 API로 넘긴다.
        skipTrailingSlashRedirect: true,
        async rewrites() {
          return [{ source: "/api/:path*", destination: "http://127.0.0.1:8790/api/:path*" }];
        },
      }),
};
export default nextConfig;
