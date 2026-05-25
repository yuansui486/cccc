import requests
import json
import os
import time

# API 服务器地址
API_BASE_URL = os.getenv("INFINITETALK_API_BASE_URL", "https://dongdongkc.shierkeji.com:6205")
API_URL = f"{API_BASE_URL}/api/v1/predict_talking_video"
STATUS_URL_TEMPLATE = f"{API_BASE_URL}/api/v1/predict_talking_video/status/{{prompt_id}}"

# 测试文件路径
IMAGE_FILE_PATH = "tmp/485628773_17900255610118845_6330545389050674094_n.jpg"
AUDIO_FILE_PATH = "tmp/output.mp3"

# 配置要测试的宽高参数
TARGET_WIDTH = 480
TARGET_HEIGHT = 640
POLL_INTERVAL = 2
POLL_TIMEOUT = 600
STATUS_REQUEST_TIMEOUT = 30
RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


def unix_ms():
    return time.time_ns() // 1_000_000


def poll_video_status(prompt_id):
    status_url = STATUS_URL_TEMPLATE.format(prompt_id=prompt_id)
    start_time = unix_ms()

    while unix_ms() - start_time < POLL_TIMEOUT * 1000:
        try:
            response = requests.get(status_url, timeout=STATUS_REQUEST_TIMEOUT)
        except requests.exceptions.RequestException as e:
            print(f"状态查询请求失败，将在 {POLL_INTERVAL} 秒后重试: {e}")
            time.sleep(POLL_INTERVAL)
            continue

        if response.status_code != 200:
            if response.status_code in RETRYABLE_STATUS_CODES:
                print(f"状态查询暂时失败 (HTTP {response.status_code})，将在 {POLL_INTERVAL} 秒后重试")
                time.sleep(POLL_INTERVAL)
                continue

            print(f"状态查询失败 (HTTP {response.status_code})")
            try:
                error_data = response.json()
                print(f"错误详情: {json.dumps(error_data, ensure_ascii=False, indent=2)}")
            except ValueError:
                print(f"服务器返回了非 JSON 格式的错误: {response.text}")
            return None

        try:
            result = response.json()
        except ValueError:
            print(f"服务器返回了非 JSON 格式的状态响应，将在 {POLL_INTERVAL} 秒后重试: {response.text}")
            time.sleep(POLL_INTERVAL)
            continue

        status = result.get("status")
        if status == "success":
            return result
        if status == "error":
            print("\n❌ ComfyUI 任务失败")
            print(f"错误详情: {json.dumps(result, ensure_ascii=False, indent=2)}")
            return None

        message = result.get("message", "任务仍在生成中")
        # print(f"{message}，{POLL_INTERVAL} 秒后继续查询...")
        time.sleep(POLL_INTERVAL)

    print(f"\n⏱️ 轮询超时，超过 {POLL_TIMEOUT} 秒仍未生成完成。")
    return None


def test_generate_video():
    print(f"正在准备向 {API_URL} 发送请求...")
    
    try:
        with open(IMAGE_FILE_PATH, "rb") as img_file, open(AUDIO_FILE_PATH, "rb") as audio_file:
            
            # 定义上传的文件
            files = {
                "image": (IMAGE_FILE_PATH, img_file, "image/jpeg"),
                "audio": (AUDIO_FILE_PATH, audio_file, "audio/wav")
            }
            
            # 定义额外传递的表单参数
            payload_data = {
                "width": TARGET_WIDTH,
                "height": TARGET_HEIGHT
            }
            
            print(f"参数配置: 宽 {TARGET_WIDTH}, 高 {TARGET_HEIGHT}")
            print("文件已加载，正在提交任务...")
            
            # 同时发送 files 和 data
            response = requests.post(API_URL, files=files, data=payload_data, timeout=60)
            
            if response.status_code in (200, 202):
                result = response.json()
                prompt_id = result.get("prompt_id")
                task_id = result.get("task_id")
                poll_id = task_id or prompt_id
                print("\n✅ 任务已被 Router 接收！")
                print("-" * 30)
                if task_id:
                    print(f"Router 任务 ID: {task_id}")
                if prompt_id:
                    print(f"ComfyUI Prompt ID: {prompt_id}")
                print(f"状态查询地址: {result.get('status_url')}")
                print("-" * 30)

                if not poll_id:
                    print("服务器响应中没有 task_id 或 prompt_id，无法继续查询状态。")
                    return

                print(f"开始每 {POLL_INTERVAL} 秒查询一次生成状态，请耐心等待...")
                video_result = poll_video_status(poll_id)
                if not video_result:
                    return

                print("\n✅ 生成成功！")
                print("-" * 30)
                if video_result.get("prompt_id"):
                    print(f"ComfyUI Prompt ID: {video_result.get('prompt_id')}")
                print(f"视频下载链接: {video_result.get('video_url')}")
                print("-" * 30)
            else:
                print(f"\n❌ 请求失败 (HTTP {response.status_code})")
                try:
                    error_data = response.json()
                    print(f"错误详情: {json.dumps(error_data, ensure_ascii=False, indent=2)}")
                except ValueError:
                    print(f"服务器返回了非 JSON 格式的错误: {response.text}")
                    
    except FileNotFoundError as e:
        print(f"\n⚠️ 找不到测试文件: {e}")
    except requests.exceptions.RequestException as e:
        print(f"\n网络请求报错: {e}")

if __name__ == "__main__":
    now = unix_ms()
    test_generate_video()
    elapsed = (unix_ms() - now) / 1000
    print(f"\n测试完成，耗时 {elapsed:.2f} 秒。")
