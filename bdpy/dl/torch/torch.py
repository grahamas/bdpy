'''PyTorch module.'''

from typing import Iterable, List, Dict, Union, Tuple, Any, Callable, Optional

import os

import numpy as np
from PIL import Image
import torch
import torch.nn as nn

from . import models

_tensor_t = Union[np.ndarray, torch.Tensor]


class FeatureExtractor(object):
    def __init__(
            self, encoder: nn.Module, layers: Iterable[str],
            layer_mapping: Optional[Dict[str, str]] = None,
            device: str = 'cpu', detach: bool = True):
        self._encoder = encoder
        self.__layers = layers
        self.__layer_map = layer_mapping
        self.__detach = detach
        self.__device = device

        self._extractor = FeatureExtractorHandle()

        self._encoder.to(self.__device)

        for layer in self.__layers:
            if self.__layer_map is not None:
                layer = self.__layer_map[layer]
            layer_object = models._parse_layer_name(self._encoder, layer)
            layer_object.register_forward_hook(self._extractor)

    def __call__(self, x: _tensor_t) -> Dict[str, _tensor_t]:
        return self.run(x)

    def run(self, x: _tensor_t) -> Dict[str, _tensor_t]:
        self._extractor.clear()
        if not isinstance(x, torch.Tensor):
            xt = torch.tensor(x[np.newaxis], device=self.__device)
        else:
            xt = x

        self._encoder.forward(xt)

        features: Dict[str, _tensor_t] = {
            layer: self._extractor.outputs[i]
            for i, layer in enumerate(self.__layers)
        }
        if self.__detach:
            features = {
                k: v.cpu().detach().numpy()
                for k, v in features.items()
            }

        return features


class FeatureExtractorHandle(object):
    def __init__(self):
        self.outputs: List[torch.Tensor] = []

    def __call__(self, module: nn.Module, module_in: Any, module_out: torch.Tensor) -> None:
        self.outputs.append(module_out.detach().clone())

    def clear(self):
        self.outputs = []


class ImageDataset(torch.utils.data.Dataset):
    '''Pytoch dataset for images.'''

    def __init__(
            self, images: List[str], labels: Optional[List[str]] = None,
            label_dirname: bool = False, resize: Optional[Tuple[int, int]] = None,
            shape: str = 'chw', transform: Optional[Callable[[_tensor_t], torch.Tensor]] = None,
            scale: float = 1, rgb_mean: Optional[List[float]] = None, preload: bool = False, preload_limit: float = np.inf):
        '''
        Parameters
        ----------
        images : List[str]
            List of image file paths.
        labels : List[str], optional
            List of image labels (default: image file names).
        label_dirname : bool, optional
            Use directory names as labels if True (default: False).
        resize : None or tuple, optional
            If not None, images will be resized by the specified size.
        shape : str ({'chw', 'hwc', ...}), optional
            Specify array shape (channel, hieght, and width).
        transform : Callable[[Union[np.ndarray, torch.Tensor]], torch.Tensor], optional
            Transformers (applied after resizing, reshaping, ans scaling to [0, 1])
        scale : optional
            Image intensity is scaled to [0, scale] (default: 1).
        rgb_mean : list([r, g, b]), optional
            Image values are centered by the specified mean (after scaling) (default: None).
        preload : bool, optional
            Pre-load images (default: False).
        preload_limit : float
            Memory size limit of preloading in GiB (default: unlimited).

        Note
        ----
        - Images are converted to RGB. Alpha channels in RGBA images are ignored.
        '''

        self.transform = transform
        # Custom transforms
        self.__shape = shape
        self.__resize = resize
        self.__scale = scale
        self.__rgb_mean = rgb_mean

        self.__data = {}
        preload_size = 0
        image_labels = []
        for i, imf in enumerate(images):
            # TODO: validate the image file
            if label_dirname:
                image_labels.append(os.path.basename(os.path.dirname(imf)))
            else:
                image_labels.append(os.path.basename(imf))
            if preload:
                data = self.__load_image(imf)
                data_size = data.size * data.itemsize
                if preload_size + data_size > preload_limit * (1024 ** 3):
                    preload = False
                    continue
                self.__data.update({i: data})
                preload_size += data_size

        self.data_path = images
        if not labels is None:
            self.labels = labels
        else:
            self.labels = image_labels
        self.n_sample = len(images)

    def __len__(self) -> int:
        return self.n_sample

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, str]:
        if idx in self.__data:
            data = self.__data[idx]
        else:
            data = self.__load_image(self.data_path[idx])

        if self.transform is not None:
            data = self.transform(data)
        else:
            data = torch.Tensor(data)

        label = self.labels[idx]

        return data, label

    def __load_image(self, fpath: str) -> np.ndarray:
        img = Image.open(fpath)

        # CMYK, RGBA --> RGB
        if img.mode == 'CMYK':
            img = img.convert('RGB')
        if img.mode == 'RGBA':
            bg = Image.new('RGB', img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])
            img = bg

        data = np.asarray(img)

        # Monotone to RGB
        if data.ndim == 2:
            data = np.stack([data, data, data], axis=2)

        # Resize the image
        if not self.__resize is None:
            data = np.array(Image.fromarray(data).resize(self.__resize, resample=2))  # bicubic

        # Reshape
        s2d = {'h': 0, 'w': 1, 'c': 2}
        data = data.transpose((s2d[self.__shape[0]],
                               s2d[self.__shape[1]],
                               s2d[self.__shape[2]]))

        # Scaling to [0, scale]
        data = (data / 255.) * self.__scale

        # Centering
        if not self.__rgb_mean is None:
            data[0] -= self.__rgb_mean[0]
            data[1] -= self.__rgb_mean[1]
            data[2] -= self.__rgb_mean[2]

        return data
