import os
import sys
import torch
import folder_paths
from PIL import Image
import numpy as np
import cv2



# Add the parent directory to the Python path so we can import from easycontrol
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from diffusers import DiffusionPipeline, FluxTransformer2DModel
from transformers import T5EncoderModel

from dreamo.dreamo_pipeline import DreamOPipeline
from dreamo.utils import img2tensor, resize_numpy_image_area, tensor2img
from tools import BEN2
from huggingface_hub import login, hf_hub_download


try:
    from facexlib.utils.face_restoration_helper import FaceRestoreHelper
except ImportError:
    FaceRestoreHelper = None  # facexlib is optional, warn if not available


# DreamO Node for ComfyUI
class DreamOLoadModelFromLocal:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "flux_model_path": ("STRING", {"default": "models/checkpoints/FLUX.1-dev", "tooltip": ""}),
                "cpu_offload": ("BOOLEAN", {"default": False}),
                "dreamo_lora": (folder_paths.get_filename_list("loras"), ),
                "dreamo_cfg_distill": (folder_paths.get_filename_list("loras"), ),
                "turbo_lora": (["None"] +folder_paths.get_filename_list("loras"), ),
                "quality_lora_pos": (["None"] + folder_paths.get_filename_list("loras"), {"tooltip": "Optional positive quality LoRA (e.g., dreamo_quality_lora_pos.safetensors)"}),
                "quality_lora_neg": (["None"] + folder_paths.get_filename_list("loras"), {"tooltip": "Optional negative quality LoRA (e.g., dreamo_quality_lora_neg.safetensors)"}),
                "int8": ("BOOLEAN", {"default": False}),
                "use_nf4": ("BOOLEAN", {"default": False}),
                "nf4_path": ("STRING", {"default": "models/checkpoints/FLUX.1-dev_Quantized_nf4", "tooltip": "GGUF模型路径，如果使用GGUF模型则需填写"}),
            }
        }

    RETURN_TYPES = ("DREAMO_PIPE",)
    FUNCTION = "load_model"
    CATEGORY = "DreamO"

    def load_model(self, flux_model_path, cpu_offload, dreamo_lora, dreamo_cfg_distill, turbo_lora, quality_lora_pos, quality_lora_neg, int8, use_nf4, nf4_path):
        device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        # Load DreamO pipeline
        # dreamo_pipeline = DreamOPipeline.from_pretrained(flux_model_path, torch_dtype=torch.bfloat16)
        orig_dreamo_pipeline = DreamOPipeline.from_pretrained(flux_model_path, torch_dtype=torch.bfloat16)

        if use_nf4 and nf4_path:
            print("Using four bit.")
            transformer = FluxTransformer2DModel.from_pretrained(
                nf4_path, subfolder="transformer", torch_dtype=torch.bfloat16
            )
            text_encoder_2 = T5EncoderModel.from_pretrained(
                nf4_path, subfolder="text_encoder_2", torch_dtype=torch.bfloat16
            )
            dreamo_pipeline = DreamOPipeline.from_pipe(
                orig_dreamo_pipeline, transformer=transformer, text_encoder_2=text_encoder_2, torch_dtype=torch.bfloat16
            )
        else:
            transformer = FluxTransformer2DModel.from_pretrained(
                flux_model_path,
                subfolder="transformer",
                torch_dtype=torch.bfloat16,
            )
            dreamo_pipeline = DreamOPipeline.from_pipe(orig_dreamo_pipeline, transformer=transformer, torch_dtype=torch.bfloat16)

        dreamo_lora_path = folder_paths.get_full_path("loras", dreamo_lora)
        dreamo_cfg_distill_path = folder_paths.get_full_path("loras", dreamo_cfg_distill)
        turbo_lora_path = folder_paths.get_full_path("loras", turbo_lora) if turbo_lora != "None" else None
        quality_lora_pos_path = folder_paths.get_full_path("loras", quality_lora_pos) if quality_lora_pos != "None" else None
        quality_lora_neg_path = folder_paths.get_full_path("loras", quality_lora_neg) if quality_lora_neg != "None" else None
        dreamo_pipeline.load_dreamo_model(device, dreamo_lora_path, dreamo_cfg_distill_path, turbo_lora_path, quality_lora_pos_path, quality_lora_neg_path)
        if int8:
            from optimum.quanto import freeze, qint8, quantize
            quantize(dreamo_pipeline.transformer, qint8)
            freeze(dreamo_pipeline.transformer)
            quantize(dreamo_pipeline.text_encoder_2, qint8)
            freeze(dreamo_pipeline.text_encoder_2)
            print('done quantize')
        if cpu_offload:
            dreamo_pipeline.enable_sequential_cpu_offload()
        else:
            dreamo_pipeline.to(device)
        return (dreamo_pipeline, )


# DreamO Node for ComfyUI
class DreamOLoadModel:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "hf_token": ("STRING", {"default": "", "multiline": True}),
                "cpu_offload": ("BOOLEAN", {"default": False}),
                "dreamo_lora": (folder_paths.get_filename_list("loras"), ),
                "dreamo_cfg_distill": (folder_paths.get_filename_list("loras"), ),
                "turbo_lora": (["None"] +folder_paths.get_filename_list("loras"), ),
                "int8": ("BOOLEAN", {"default": False}),
            }
        }

    RETURN_TYPES = ("DREAMO_PIPE",)
    FUNCTION = "load_model"
    CATEGORY = "DreamO"

    def load_model(self, hf_token, cpu_offload, dreamo_lora, dreamo_cfg_distill, turbo_lora, int8):
        login(token=hf_token)
        device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        # Load DreamO pipeline
        model_root = 'black-forest-labs/FLUX.1-dev'
        cache_dir = folder_paths.get_folder_paths("diffusers")[0]

        dreamo_pipeline = DreamOPipeline.from_pretrained(model_root, torch_dtype=torch.bfloat16, cache_dir=cache_dir)

        dreamo_lora_path = folder_paths.get_full_path("loras", dreamo_lora)
        dreamo_cfg_distill_path = folder_paths.get_full_path("loras", dreamo_cfg_distill)
        turbo_lora_path = folder_paths.get_full_path("loras", turbo_lora) if turbo_lora != "None" else None
        dreamo_pipeline.load_dreamo_model(device, dreamo_lora_path, dreamo_cfg_distill_path, turbo_lora_path)
        if int8:
            from optimum.quanto import freeze, qint8, quantize
            quantize(dreamo_pipeline.transformer, qint8)
            freeze(dreamo_pipeline.transformer)
            quantize(dreamo_pipeline.text_encoder_2, qint8)
            freeze(dreamo_pipeline.text_encoder_2)
            print('done quantize')
        if cpu_offload:
            dreamo_pipeline.enable_sequential_cpu_offload()
        else:
            dreamo_pipeline.to(device)
        return (dreamo_pipeline, )

class DreamOGenerate:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "pipe": ("DREAMO_PIPE",),
                "bg_rm_model": ("BG_RM_MODEL",),
                "face_helper": ("FACE_HELPER",),
                "ref_image1": ("IMAGE",),
                "ref_task1": (['ip', 'id', 'style'],),
                "prompt": ("STRING", {"multiline": True}),
                "width": ("INT", {"default": 1024, "min": 256, "max": 2048, "step": 16}),
                "height": ("INT", {"default": 1024, "min": 256, "max": 2048, "step": 16}),
                "num_steps": ("INT", {"default": 12, "min": 1, "max": 100, "step": 1}),
                "guidance": ("FLOAT", {"default": 3.5, "min": 0.0, "max": 10.0, "step": 0.1}),
                "seed": ("INT", {"default": -1, "min": -1, "max": 0xffffffffffffffff}),
            },
            "optional": {
                "ref_image2": ("IMAGE",),
                "ref_task2": (['ip', 'id', 'style'],),
            }
        }

    RETURN_TYPES = ("IMAGE", )  # output_image, debug_image, used_seed
    FUNCTION = "generate"
    CATEGORY = "DreamO"

    def generate(self, pipe, bg_rm_model, face_helper, ref_image1, ref_task1, prompt, width, height, num_steps, guidance, seed, ref_image2=None, ref_task2=None):
        ref_res = 512
        true_cfg = 1.0
        cfg_start_step = 0
        cfg_end_step = 0
        neg_prompt = ""
        neg_guidance = 3.5
        first_step_guidance = 0
        dreamo_pipeline = pipe
        device = "cuda" if torch.cuda.is_available() else "cpu"
        ref_conds = []
        debug_images = []
        ref_images = [ref_image1, ref_image2]
        ref_tasks = [ref_task1, ref_task2]
        for idx, (ref_image, ref_task) in enumerate(zip(ref_images, ref_tasks)):
            if ref_image is not None:
                img = ref_image
                if isinstance(img, torch.Tensor):
                    img = img[0].cpu().numpy() if img.dim() == 4 else img.cpu().numpy()
                    img = (img * 255).astype(np.uint8)
                elif isinstance(img, np.ndarray):
                    img = Image.fromarray((img * 255).astype(np.uint8))
                if ref_task == "id":
                    if face_helper is not None:
                        face_helper.clean_all()
                        image_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
                        face_helper.read_image(image_bgr)
                        face_helper.get_face_landmarks_5(only_center_face=True)
                        face_helper.align_warp_face()
                        if len(face_helper.cropped_faces) == 0:
                            continue
                        align_face = face_helper.cropped_faces[0]
                        input_tensor = img2tensor(align_face, bgr2rgb=True).unsqueeze(0) / 255.0
                        input_tensor = input_tensor.to(device)
                        from torchvision.transforms.functional import normalize
                        parsing_out = face_helper.face_parse(normalize(input_tensor, [0.485, 0.456, 0.406], [0.229, 0.224, 0.225]))[0]
                        parsing_out = parsing_out.argmax(dim=1, keepdim=True)
                        bg_label = [0, 16, 18, 7, 8, 9, 14, 15]
                        bg = sum(parsing_out == i for i in bg_label).bool()
                        white_image = torch.ones_like(input_tensor)
                        face_features_image = torch.where(bg, white_image, input_tensor)
                        face_features_image = tensor2img(face_features_image, rgb2bgr=False)
                        img = face_features_image
                elif ref_task != "style":
                    if bg_rm_model is not None:
                        img = bg_rm_model.inference(Image.fromarray(img))
                img = resize_numpy_image_area(np.array(img), ref_res * ref_res)
                debug_images.append(img)
                img_tensor = img2tensor(img, bgr2rgb=False).unsqueeze(0) / 255.0
                img_tensor = 2 * img_tensor - 1.0
                ref_conds.append({
                    'img': img_tensor.to(device),
                    'task': ref_task,
                    'idx': idx + 1,
                })
        used_seed = int(seed)
        if used_seed == -1:
            used_seed = torch.seed()
        image = dreamo_pipeline(
            prompt=prompt,
            width=width,
            height=height,
            num_inference_steps=num_steps,
            guidance_scale=guidance,
            ref_conds=ref_conds,
            generator=torch.Generator(device="cpu").manual_seed(used_seed),
            true_cfg_scale=true_cfg,
            true_cfg_start_step=cfg_start_step,
            true_cfg_end_step=cfg_end_step,
            negative_prompt=neg_prompt,
            neg_guidance_scale=neg_guidance,
            first_step_guidance_scale=first_step_guidance if first_step_guidance > 0 else guidance,
        ).images[0]
        # Convert to tensor for ComfyUI
        image = np.array(image) / 255.0
        image = torch.from_numpy(image).float()
        if image.dim() == 3:
            image = image.unsqueeze(0)
        # Also convert debug_images to tensors
        # debug_images_tensor = [torch.from_numpy(np.array(img) / 255.0).float().unsqueeze(0) if np.array(img).ndim == 3 else torch.from_numpy(np.array(img) / 255.0).float() for img in debug_images]
        return (image, )

class FaceModelLoad:
    @classmethod
    def INPUT_TYPES(cls):
        return {}

    RETURN_TYPES = ("FACE_HELPER",)
    FUNCTION = "load_face_model"
    CATEGORY = "DreamO"

    def load_face_model(self):
        device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        face_helper = FaceRestoreHelper(
                upscale_factor=1,
                face_size=512,
                crop_ratio=(1, 1),
                det_model='retinaface_resnet50',
                save_ext='png',
                device=device,
            )
        return (face_helper,)

class BgRmModelLoad:
    @classmethod
    def INPUT_TYPES(cls):
        return {}

    RETURN_TYPES = ("BG_RM_MODEL",)
    FUNCTION = "load_bg_rm_model"
    CATEGORY = "DreamO"

    def load_bg_rm_model(self):
        device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        bg_rm_model = BEN2.BEN_Base().to(device).eval()
        hf_hub_download(repo_id='PramaLLC/BEN2', filename='BEN2_Base.pth', local_dir='models')
        bg_rm_model.loadcheckpoints('models/BEN2_Base.pth')
        return (bg_rm_model,)

