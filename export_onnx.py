#!/usr/bin/env python
# -*- encoding : utf-8 -*-
"""
@Author :sunjunyi
@Time   :2023/8/3 13:53
"""
import os
import yaml
import onnxruntime
import torch
import numpy as np
from transformers import AutoProcessor
from transformers.models.clip import CLIPTextModelWithProjection

# 加载配置文件
with open('config.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

# 预处理器
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
processor = AutoProcessor.from_pretrained(config['onnx']['checkpoint_dir'])
text_encoder_hf = CLIPTextModelWithProjection.from_pretrained(config['onnx']['checkpoint_dir']).to(device)
model_name = config['onnx']['checkpoint_dir'].split('/')[1]

# bin转pt
os.makedirs('./onnx', exist_ok=True)
torch.save(text_encoder_hf, f'./onnx/{model_name}_text_encoder.pt')
text_encoder_pt = torch.load(f'./onnx/{model_name}_text_encoder.pt')

# pt转onnx
text = processor(text='hello', return_tensors='pt', max_length=77, padding=True).to(device)
text = dict(text)
torch.onnx.export(
    model = text_encoder_pt,
    args=text,
    f = f'./onnx/{model_name}_text_encoder.onnx',
    opset_version=14,
    input_names=['input_ids', 'attention_mask'],
    output_names=['text_embeds', 'last_hidden_state'],
    dynamic_axes={
        'input_ids': {0: 'batch_size'},
        'attention_mask': {0: 'batch_size'},
        'text_embeds': {0: 'batch_size'},
        'last_hidden_state': {0: 'batch_size'}
    }
)

# pytorch推理时间
out = text_encoder_hf(**text)

# onnx推理时间
providers = 'CUDAExecutionProvider' if torch.cuda.is_available() else 'CPUExecutionProvider'
session = onnxruntime.InferenceSession(f'./onnx/{model_name}_text_encoder.onnx', providers=[providers])
onnx_text = processor(text='hello', return_tensors='np', max_length=77, padding=True)
onnx_text = dict(onnx_text)
for i in onnx_text:
    onnx_text[i] = onnx_text[i].astype(np.int64)
out = session.run(None, onnx_text)[0]
