import json
from urllib.parse import urlencode

import requests
from fastapi import fastapi, uploadfile, file, form
from fastapi.responses import jsonresponse
import uvicorn

app = fastapi(title="comfyui infinitetalk api wrapper")

# ================= 配置区 =================
comfyui_server = "127.0.0.1:8080"  # 你的 comfyui 服务器地址
workflow_file = "kijai-wanvideo_i2v_infinitetalk_example_01.json"  # 保存的 api 格式 json 文件路径

# 根据你提供的数据锁定的节点 id
image_node_id = "284"   # loadimage 节点
audio_node_id = "125"   # loadaudio 节点
video_node_id = "131"   # vhs_videocombine 节点
width_node_id = "245"   # 宽度 intconstant 节点
height_node_id = "246"  # 高度 intconstant 节点
# ==========================================


def upload_to_comfyui(file_bytes: bytes, filename: str) -> str:
    """将文件上传到 comfyui 的输入目录"""
    url = f"http://{comfyui_server}/upload/image"
    files = {"image": (filename, file_bytes)}
    try:
        response = requests.post(url, files=files, timeout=30)
        if response.status_code == 200:
            return response.json().get("name")
        else:
            raise exception(f"comfyui 拒绝文件上传: {response.text}")
    except exception as e:
        raise exception(f"连接 comfyui 上传失败: {str(e)}")


def queue_prompt(prompt_json: dict) -> str:
    """向 comfyui 队列提交任务，返回 prompt_id"""
    url = f"http://{comfyui_server}/prompt"
    payload = {"prompt": prompt_json}
    try:
        response = requests.post(url, json=payload, timeout=30)
        if response.status_code == 200:
            prompt_id = response.json().get("prompt_id")
            if not prompt_id:
                raise exception("comfyui 返回结果中没有 prompt_id")
            return prompt_id
        else:
            raise exception(f"comfyui 拒绝提交任务: {response.text}")
    except exception as e:
        raise exception(f"无法提交任务到 comfyui: {str(e)}")


def build_video_url(video_info: dict) -> str:
    """根据 comfyui 输出信息拼接可访问的视频 url"""
    query_params = {
        "filename": video_info.get("filename", ""),
        "subfolder": video_info.get("subfolder", ""),
        "type": video_info.get("type", "temp"),
    }

    for key, value in video_info.items():
        if value is not none and key not in query_params:
            query_params[key] = value

    query = urlencode(query_params)
    return f"http://{comfyui_server}/api/view?{query}"


def get_video_status(prompt_id: str) -> dict:
    """查询 comfyui 历史记录，返回任务状态或视频 url"""
    url = f"http://{comfyui_server}/history/{prompt_id}"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            raise exception(f"comfyui 状态查询失败: {response.text}")

        history = response.json()
        if prompt_id not in history:
            return {
                "status": "processing",
                "prompt_id": prompt_id,
                "message": "任务仍在队列或生成中",
            }

        task_result = history[prompt_id]
        outputs = task_result.get("outputs", {})
        video_node_output = outputs.get(video_node_id, {})
        video_outputs = video_node_output.get("gifs") or video_node_output.get("videos") or []

        if video_outputs:
            return {
                "status": "success",
                "prompt_id": prompt_id,
                "video_url": build_video_url(video_outputs[0]),
            }

        task_status = task_result.get("status", {})
        status_text = task_status.get("status_str")
        if status_text and status_text != "success":
            return {
                "status": "error",
                "prompt_id": prompt_id,
                "message": f"comfyui 任务状态异常: {status_text}",
                "detail": task_status,
            }

        return {
            "status": "error",
            "prompt_id": prompt_id,
            "message": "任务已完成，但未在视频合并节点中找到输出文件。",
        }
    except exception as e:
        raise exception(f"无法查询 comfyui 任务状态: {str(e)}")


@app.post("/api/v1/predict_talking_video")
async def generate_talking_video(
    image: uploadfile = file(..., description="输入的单张人物图片"),
    audio: uploadfile = file(..., description="输入的对口型音频文件"),
    width: int = form(480, description="视频宽度，默认 480"),
    height: int = form(640, description="视频高度，默认 640")
):
    """
    接收图片、音频及宽高参数，提交生成任务并立即返回 prompt_id
    """
    try:
        # 1. 读文件
        image_bytes = await image.read()
        audio_bytes = await audio.read()

        # 2. 传到 comfyui
        comfy_image_name = upload_to_comfyui(image_bytes, image.filename)
        comfy_audio_name = upload_to_comfyui(audio_bytes, audio.filename)

        # 3. 加载本地 json
        try:
            with open(workflow_file, "r", encoding="utf-8") as f:
                prompt_json = json.load(f)
        except filenotfounderror:
            return jsonresponse(status_code=500, content={"status": "error", "message": "未找到 workflow_api.json"})

        # 4. 注入动态参数 (图片、音频、宽、高)
        try:
            prompt_json[image_node_id]["inputs"]["image"] = comfy_image_name
            prompt_json[audio_node_id]["inputs"]["audio"] = comfy_audio_name
            prompt_json[width_node_id]["inputs"]["value"] = width
            prompt_json[height_node_id]["inputs"]["value"] = height
        except keyerror as e:
            return jsonresponse(status_code=500, content={"status": "error", "message": f"json 工作流节点缺失: {e}"})

        # 5. 提交任务，立即返回 prompt_id
        prompt_id = queue_prompt(prompt_json)

        return jsonresponse(
            status_code=200,
            content={
                "status": "submitted",
                "prompt_id": prompt_id,
                "status_url": f"/api/v1/predict_talking_video/status/{prompt_id}",
            },
        )

    except exception as e:
        return jsonresponse(status_code=500, content={"status": "error", "message": str(e)})


@app.get("/api/v1/predict_talking_video/status/{prompt_id}")
async def get_talking_video_status(prompt_id: str):
    """
    根据 prompt_id 查询生成状态。客户端可每 2 秒调用一次。
    """
    try:
        return jsonresponse(status_code=200, content=get_video_status(prompt_id))
    except exception as e:
        return jsonresponse(status_code=500, content={"status": "error", "message": str(e)})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)