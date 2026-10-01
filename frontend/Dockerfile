FROM node:26-alpine

WORKDIR /app

# 复制依赖文件
COPY package.json pnpm-lock.yaml ./

# 与 packageManager 字段保持一致，确保容器构建可复现
RUN npm install -g pnpm@10.6.3

# 清理 npm 缓存并安装依赖
RUN pnpm install --frozen-lockfile

# 复制源代码
COPY . .

EXPOSE 5173

# 运行开发服务器
CMD ["pnpm", "run", "dev", "--host"]
