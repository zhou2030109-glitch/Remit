FROM node:20-alpine

WORKDIR /app

# 复制依赖文件
COPY package.json pnpm-lock.yaml ./

# 安装 pnpm
RUN npm install -g pnpm

# 清理 npm 缓存并安装依赖
RUN pnpm install --frozen-lockfile

# 复制源代码
COPY . .

EXPOSE 5173

# 运行开发服务器
CMD ["pnpm", "run", "dev", "--host"]