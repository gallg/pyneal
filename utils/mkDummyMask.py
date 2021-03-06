"""
Tool to quickly make a dummy mask with user-supplied dimensions

The resulting mask will be a rectangle (.25*xDim X .25*yDim) positioned in the
middle of the middle slice of the given volume dimensions
"""
import os
from os.path import join
import sys
import argparse

import nibabel as nib
import numpy as np


def mkDummyMask(dims, outputDir):
    """ Make a dummy mask of given dims

    Parameters
    ----------
    dims : int list (length = 3)
        [x, y, z] dimensions of the output mask
    outputDir : string
        full path to where the output mask will be saved

    """
    assert len(dims) == 3, 'Too many dimensions specified!'

    # volume dims
    x,y,z = dims
    print('mask dimensions: [{}, {}, {}]'.format(x,y,z))

    # confirm output path is a real path
    if not os.path.exists(outputDir):
        print('Output path does not exist: {}'.format(outputDir))
        sys.exit()

    # make array of zeros
    maskArray = np.zeros(shape=[x,y,z])

    # make a square in the middle slice of 1s. this will be the mask
    mask_sizeX = np.floor(x/4)
    mask_sizeY = np.floor(y/4)

    maskStartX = int(np.floor(x/2) - mask_sizeX/2)
    maskEndX = int(maskStartX + mask_sizeX)
    maskStartY = int(np.floor(y/2) - mask_sizeY/2)
    maskEndY = int(maskStartY + mask_sizeY)

    maskArray[maskStartX:maskEndX, maskStartY:maskEndY, int(np.floor(z/2))] = 1

    # save as nib object
    maskImage = nib.Nifti1Image(maskArray, affine=np.eye(4))
    outputName = 'dummyMask_{}-{}-{}.nii.gz'.format(x,y,z)
    outputPath = join(outputDir, outputName)
    nib.save(maskImage, outputPath)
    print('dummy mask saved as: {}'.format(outputPath))


if __name__ == '__main__':
    # parse arguments
    parser = argparse.ArgumentParser()
    parser.add_argument('maskDims',
                         metavar='dim',
                         type=int,
                         nargs=3,
                         help='volume dims: x y z')
    parser.add_argument('-o', '--outputDir',
                        default='.',
                        type=str,
                        help='output dir path for saving mask')

    args = parser.parse_args()
    print(args)

    mkDummyMask(args.maskDims, args.outputDir)
