#!/usr/bin/env python
# -*- encoding : utf-8 -*-
"""
@Author :sunjunyi
@Time   :2023/7/29 13:19
"""
import os
import json
import pickle

import yaml

import torch
import gradio as gr
import numpy as np
import pandas as pd

from onnx_model import OnnxModel, HfModel
from redis_handler import RedisHandler
from metric import compute_mrr
from PIL import Image
from pymilvus import MilvusClient, connections, FieldSchema, CollectionSchema, DataType, Collection, utility
from transformers import AutoModel, AutoProcessor, CLIPTextModelWithProjection

# 加载配置文件
with open('config.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

root_dir = './mini-imagenet/'
with open(os.path.join(root_dir, 'classes_name.json'), 'r') as f:
    mini_imagenet_label = json.load(f)

# 文本标签到数字的字典
label2cate = {i[1].replace('_', ' '): i[0] for i in list(mini_imagenet_label.values())}

# 文本标签列表
labels = [i[1].replace('_', ' ') for i in list(mini_imagenet_label.values())]

# 标签描述文件
captions = ['this is a picture of ' + i for i in labels]


# 图像id到图像文件的映射函数
def id2image(img_id):
    root_dir = './mini-imagenet/'
    val_data = pd.read_csv(os.path.join(root_dir, 'new_val.csv'))  # 只对12000张测试集的图像做检索，
    img_path = os.path.join(root_dir, 'images', val_data['filename'][img_id])
    img = Image.open(img_path)
    return img


class ModelQuery:
    def __init__(self, model_name, use_onnx=config['onnx']['use_onnx'], use_redis=config['redis']['use_redis']):
        self.model_name = model_name
        self.use_onnx = use_onnx
        self.use_redis = use_redis

        if self.use_redis:
            self.redis_handler = RedisHandler()

        self.model = OnnxModel(model_name) if self.use_onnx else HfModel(model_name)
        self._connect_milvus()
        # self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        # self.model, self.processor = self._load_model_and_processor(model_name)
        # self._warmup()

    # 加载模型和预处理器
    # def _load_model_and_processor(self, model_name):
    #     model = CLIPTextModelWithProjection.from_pretrained(model_name).to(self.device)
    #     processor = AutoProcessor.from_pretrained(model_name)
    #     return model, processor
    #
    # # 加载模型时 先warmup一下 避免首次推理时间长
    # def _warmup(self):
    #     input = self.processor(text='warmup text', return_tensors='pt', padding=True).to(self.device)
    #     self.model.eval()
    #     with torch.no_grad():
    #         self.model(**input)
    #
    # # 清空显存
    # @classmethod
    # def _empty_cache(self):
    #     torch.cuda.empty_cache()
    #
    # # 重新加载模型和预处理器
    # def _reload(self, model_name):
    #     self._empty_cache()
    #     self.model_name = model_name
    #     self.model, self.processor = self._load_model_and_processor(model_name)

    def _connect_milvus(self):
        connections.connect(host=config['milvus']['host'], port=config['milvus']['port'])
        self.collection = Collection(config['milvus']['collection_name'])
        self.collection.load()

    def __call__(self, query_text, topk, model_name, return_metrics=False):
        # if not self.use_onnx and self.model_name != model_name:
        #     self._reload(model_name)

        # if self.use_onnx:
        # text_embeds = self.model(text=query_text)
        # else:
        # text_input = self.processor(text=query_text, return_tensors='pt', padding=True).to(self.device)
        # self.model.eval()
        # with torch.no_grad():
        #     output = self.model(**text_input)
        #
        # # 文本embeds向量归一化
        # output.text_embeds /= output.text_embeds.norm(dim=-1, keepdim=True)
        # text_embeds = output.text_embeds.cpu().numpy()

        if return_metrics:
            recalls, mrrs = self._compute_metrics(query_text)
            return recalls, mrrs
        else:
            ids, distances, categories = self._search_categories(query_text, topk)
            images = list(map(id2image, ids))
            captions = list(map(lambda x: labels[x], categories))
            return list(zip(images, captions))

    # 根据 单个文本向量搜索topk个结果
    def _search_categories(self, query_text, topk):
        if self.use_redis:
            # 如果查询文本是单个字符串
            if type(query_text) == str:
                search_res = self.redis_handler.get(query_text)
            # 如果查询文本是字符串列表
            elif type(query_text) == list:
                search_res = self.redis_handler.redis_client.mget(query_text)
            else:
                return EOFError

            # 如果没在redis中找到结果
            if search_res == None:
                text_embeds = self.model(text=query_text)
                res = self.collection.search(
                    data=text_embeds,
                    anns_field='embedding',
                    param=config['milvus']['search_params'],
                    limit=topk,
                    output_fields=['category']
                )

                ids = [list(hits.ids) for hits in res]
                distances = [list(hits.distances) for hits in res]
                categories = [[hit.entity.get('category') for hit in hits] for hits in res]

                res_pack = list(zip(ids, distances, categories))
                if type(query_text) == str:
                    query_text = [query_text]
                for text, pack in zip(query_text, res_pack):
                    self.redis_handler.set(text, pickle.dumps(pack))
            else:
                if type(query_text) == str:
                    id, distance, category = self.redis_handler.get(query_text)
                    return id, distance, category
                elif type(query_text) == list:
                    deserialize_res = self.redis_handler.mget(query_text)
                    ids, distances, categories = tuple(zip(*deserialize_res))
                    return ids, distances, categories

        # text_embeds = self.model(text=query_text)
        # res = self.collection.search(
        #     data=text_embeds,
        #     anns_field='embedding',
        #     param=config['milvus']['search_params'],
        #     limit=topk,
        #     output_fields=['category']
        # )
        #
        # ids = [list(hits.ids) for hits in res]
        # distances = [list(hits.distances) for hits in res]
        # categories = [[hit.entity.get('category') for hit in hits] for hits in res]

        # if type(query_text) != str:
        #     return ids, distances, categories
        # else:
        #     return ids[0], distances[0], categories[0]

    # 计算相关指标
    def _compute_metrics(self, query_text):
        from sklearn.metrics import precision_score, recall_score
        recalls, mrrs = [], []
        topk_list = [1, 3, 5, 10]
        ids, _, categories = self._search_categories(query_text, max(topk_list))
        for k in topk_list:
            targets = np.array([i for i in range(100)]).repeat(k)
            categories_flat = np.array(categories)[:, :k].flatten()

            recall = recall_score(targets, categories_flat, average='micro')
            mrr = compute_mrr([i for i in range(100)], np.array(categories)[:, :k])

            recalls.append(round(100 * recall, 4))
            mrrs.append(round(100 * mrr, 4))
        return recalls, mrrs


class CalMetrics:
    def __init__(self, modelquery):
        self.modelquery = modelquery

    def __call__(self):
        recalls, mrrs = self.modelquery(query_text=labels, topk=10, model_name=self.modelquery.model_name,
                                        return_metrics=True)
        return f"""
                |            | **Recall (%)** | **mAP (%)** |
                |:----------:|:--------------:|:-----------:|
                |  **top@1** |{recalls[0]}    |{mrrs[0]}    |
                |  **top@3** |{recalls[1]}    |{mrrs[1]}    |
                |  **top@5** |{recalls[2]}    |{mrrs[2]}    |
                | **top@10** |{recalls[3]}    |{mrrs[3]}    |
                """


def text2image_gr():
    clip = config['gradio']['checkpoint_dir']
    # blip2 = 'blip2-2.7b'

    title = "<h1 align='center'>多模态大模型图像检索应用</h1>"
    description = '本项目基于mini imagenet数据集微调'

    examples = [
        ["dugong", 10, clip, ],
        ["robin", 10, clip, ],
        ["triceratops", 10, clip, ],
        ["green mamba", 10, clip, ]
    ]

    with gr.Blocks() as demo:
        gr.Markdown(title)
        gr.Markdown(description)
        with gr.Row():
            with gr.Column(scale=1):
                with gr.Column(scale=2):
                    query_text = gr.Textbox(value="house finch", label="请填写搜索文本", elem_id=0, interactive=True)

                # 注意topk < search_param的ef
                topk = gr.components.Slider(minimum=1, maximum=20, step=1, value=10, label="返回图片数",
                                            elem_id=2)

                model_name = gr.components.Radio(label="模型选择", choices=[clip],
                                                 value=clip, elem_id=3)

                btn1 = gr.Button("搜索")

            with gr.Column(scale=100):
                out1 = gr.Gallery(label="检索结果为:", columns=5, height=200)

            with gr.Column(scale=2):
                with gr.Column(scale=6):
                    out2 = gr.Markdown(
                        """
                        |            | **Recall (%)** | **mAP (%)** |
                        |:----------:|:--------------:|:-----------:|
                        |  **top@1** |                |             |
                        |  **top@3** |                |             |
                        |  **top@5** |                |             |
                        | **top@10** |                |             |
                        """
                    )
                btn2 = gr.Button("计算检索100类的平均指标", scale=1)

        inputs = [query_text, topk, model_name]

        gr.Examples(examples, inputs=inputs)

        # TODO: 添加推理时间 查询时间的显示框 datatime库 timeit库
        model_query = ModelQuery(model_name.value)
        cal_metrics = CalMetrics(model_query)

        btn1.click(fn=model_query, inputs=inputs, outputs=out1)
        btn2.click(fn=cal_metrics, inputs=None, outputs=out2)

    return demo


if __name__ == "__main__":
    with gr.TabbedInterface(
            [text2image_gr()],
            ["文到图搜索"],
    ) as demo:
        demo.launch(
            enable_queue=True,
            server_name='0.0.0.0'
            # share=True
        )
