# devresize.py

## 介绍

通常在控制台扩容云硬盘，只扩充了硬盘设备的大小，硬盘上的文件系统对这个扩容操作无感知。
因此要使用扩充的这部分容量，还需要对云盘上的 **文件系统** 进行扩容。
本目录下的devresize.py脚本可用于在云盘扩容后，自动扩充云服务器 **数据盘** 上的 **文件系统**。

## 适用场景

目前脚本只能用于自动扩容 **Linux系统** 下，符合以下两种情况之一的 **数据盘**：
1. 未分区，直接使用裸盘格式化文件系统（如`mkfs.ext2 /dev/vdb`），且文件系统类型为 ext2/3/4 或 xfs 的云盘。
2. 云盘使用 **MBR格式的分区表**，只创建了一个主分区，且该分区文件系统类型为 ext2/3/4 或 xfs 的云盘。

其它情况可以参考[扩容Linux文件系统](https://cloud.tencent.com/document/product/362/6738)文档手动扩容。

## 云盘扩容步骤

1. 首先参考[扩容云硬盘](https://cloud.tencent.com/document/product/362/5747)文档，在控制台或通过API对云盘容量进行扩容
2. 此时请务必 **对扩容后的云盘制作快照**，以防后续扩容文件系统时丢失数据！
3. 对云盘容量进行扩容并制作快照后，还需要云盘上的扩充文件系统大小。若云盘符合上述适用场景，可以下载本脚本执行命令`python devresize.py {云硬盘设备路径}`对特定云盘进行扩容；若不符合适用场景，请参考相关文档进行手动扩容。

## 相关文档

[扩容云硬盘](https://cloud.tencent.com/document/product/362/5747)

[创建快照文档](https://cloud.tencent.com/document/product/362/5755)

[扩容Linux文件系统](https://cloud.tencent.com/document/product/362/6738)

[扩容Windows文件系统](https://cloud.tencent.com/document/product/362/6737)
