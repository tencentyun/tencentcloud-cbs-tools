#!/usr/bin/env python2.7
# coding: utf-8
import unittest
import sys
import os
import time
import commands

from devresize import main, write_mbr, read_ub, read_us, part_probe

DEVICE = "/dev/vdb"


class TestDeviceResize(unittest.TestCase):

  def __init__(self, *args, **kwargs):
    super(TestDeviceResize, self).__init__(*args, **kwargs)
    self.device = DEVICE
    self.partition = self.device + "1"

  def setUp(self):
    time.sleep(1)     # 避免Device is busy错误
    print '\n', self._testMethodName


  def _part_probe(self):
    fd = open(self.device, 'r+')
    part_probe(fd)
    fd.close()


  def _make_label(self, label="msdos"):
    self.assertEqual(commands.getstatusoutput("parted -s %s mklabel %s" % (self.device, label))[0], 0)


  def _make_part(self, label="msdos"):
    "将盘格式化为只有一个分区，分区大小为块设备大小的一半"
    self._make_label(label=label)
    self.assertEqual(commands.getstatusoutput("parted -s -a minimal %s mkpart primary ext4 0 50%%" % self.device)[0], 0)


  def test_file_system_ext2(self):
    "测试不同文件系统"
    fs = 'ext2'
    self._make_part()
    self.assertEqual(commands.getstatusoutput("mkfs.%s -F %s" % (fs, self.partition))[0], 0)
    self._part_probe()
    output = commands.getoutput("python devresize.py -f %s" % self.device)
    self.assertTrue("[INFO] - Finished" in output, msg="测试 %s 类型文件系统" % fs)


  def test_file_system_ext3(self):
    "测试不同文件系统"
    fs = 'ext3'
    self._make_part()
    self.assertEqual(commands.getstatusoutput("mkfs.%s -F %s" % (fs, self.partition))[0], 0)
    self._part_probe()
    output = commands.getoutput("python devresize.py -f %s" % self.device)
    self.assertTrue("[INFO] - Finished" in output, msg="测试 %s 类型文件系统" % fs)


  def test_file_system_ext4(self):
    "测试不同文件系统"
    fs = 'ext4'
    self._make_part()
    self.assertEqual(commands.getstatusoutput("mkfs.%s -F %s" % (fs, self.partition))[0], 0)
    self._part_probe()
    output = commands.getoutput("python devresize.py -f %s" % self.device)
    self.assertTrue("[INFO] - Finished" in output, msg="测试 %s 类型文件系统" % fs)

  
  def test_file_system_xfs(self):
    "测试不同文件系统"
    fs = 'xfs'
    self._make_part()
    self.assertEqual(commands.getstatusoutput("mkfs.%s -f %s" % (fs, self.partition))[0], 0)
    self._part_probe()
    output = commands.getoutput("python devresize.py -f %s" % self.device)
    self.assertTrue("[INFO] - Finished" in output, msg="测试 %s 类型文件系统" % fs)


  def test_fs_block_size_4K(self):
    "测试不同块大小"
    bs = '4096'
    self._make_part()
    self.assertEqual(commands.getstatusoutput("mkfs.ext3 -F -b %s %s" % (bs, self.partition))[0], 0)
    self._part_probe()
    output = commands.getoutput("python devresize.py -f %s" % self.device)
    self.assertTrue("[INFO] - Finished" in output, msg="测试块大小=%s" % bs)


  def test_no_part(self):
    """测试裸盘作为文件系统"""
    self._make_label()
    self.assertEqual(commands.getstatusoutput("mkfs.ext3 -F %s" % self.device)[0], 0)
    self.assertEqual(commands.getstatusoutput("resize2fs %s 10G" % self.device)[0], 0)  
    self._part_probe()
    output = commands.getoutput("python devresize.py -f %s" % self.device)
    self.assertTrue("[INFO] - Finished" in output, msg="测试裸盘作为文件系统")


  def test_one_part(self):
    """测试不同分区个数"""
    self._make_part()
    self.assertEqual(commands.getstatusoutput("mkfs.ext3 -F %s" % self.partition)[0], 0)
    self._part_probe()
    output = commands.getoutput("python devresize.py -f %s" % self.device)
    self.assertTrue("[INFO] - Finished" in output, msg="测试只有一个分区，且为主分区的盘")


  def test_multiple_part(self):
    """测试多于一个分区的盘"""
    self._make_part()
    self.assertEqual(commands.getstatusoutput("parted -s %s mkpart primary ext4 50%% 60%%" % self.device)[0], 0)
    self._part_probe()
    output = commands.getoutput("python devresize.py -f %s" % self.device)
    self.assertTrue("[ERROR] - Disk %s has multiple partitions." % self.device in output, msg="测试多于一个分区的盘")


  def test_mbr_error(self):
    """测试MBR分区文件系统格式标识错误"""
    self._make_part()
    fd = open(self.device, 'r+')
    data = fd.read(512)
    temp_data = data
    self.assertEqual(read_ub(temp_data[446+4]), 0x83)
    temp_data = temp_data[:446+4] + '\0' + temp_data[446+4+1:]
    write_mbr(fd, temp_data)
    output = commands.getoutput("python devresize.py -f %s" % self.device)

    self.assertTrue("[ERROR] - Disk %s has invalid partition" % self.device in output, msg="测试MBR分区文件系统格式标识错误")


  def test_mounted(self):
    """测试磁盘已有分区mount"""
    if not os.path.exists("mp"):
      os.mkdir("mp")
    self._make_part()
    self.assertEqual(commands.getstatusoutput("mkfs.ext4 -F %s" % self.partition)[0], 0)
    self._part_probe()
    self.assertEqual(commands.getstatusoutput("mount %s mp" % self.partition)[0], 0)
    output = commands.getoutput("python devresize.py -f %s" % self.device)
    self.assertEqual(commands.getstatusoutput("umount mp")[0], 0)
    self.assertTrue("[ERROR] - Target partition %s must be unmounted." % self.partition in output, msg="测试磁盘已有分区mount")


  def test_no_freespace(self):
    """测试磁盘未扩容"""
    self._make_label()
    self.assertEqual(commands.getstatusoutput("mkfs.ext4 -F %s" % self.device)[0], 0)
    self._part_probe()
    output = commands.getoutput("python devresize.py -f %s" % self.device)
    self.assertTrue("Nothing to do!" in output, msg="测试磁盘未扩容")


  def test_unsupport_filesystem(self):
    """测试不支持的文件系统"""
    self._make_label()
    self.assertTrue(
      0 in 
      [commands.getstatusoutput("mkfs.btrfs %s" % self.device)[0], 
      commands.getstatusoutput("mkfs.btrfs -f %s" % self.device)[0]]
    )
    self._part_probe()
    output = commands.getoutput("python devresize.py -f %s" % self.device)
    self.assertTrue("[ERROR] - Only can process ext2/3/4" in output, msg="测试不支持的文件系统")


  def test_unsupport_blocksize(self):
    """测试不支持的文件系统块大小"""
    self._make_label()
    self.assertEqual(commands.getstatusoutput("mkfs.ext4 -F -b 1024 %s" % self.device)[0], 0)
    self._part_probe()
    output = commands.getoutput("python devresize.py -f %s" % self.device)
    self.assertTrue("[ERROR] - Only can process filesystem with block size 4KB" in output, msg="测试不支持的文件系统块大小")
    
  
  def test_not_root(self):
    """测试非root权限执行扩容脚本"""
    self._make_label()
    output = commands.getoutput("su testuser -c 'python devresize.py -f %s'" % self.device)
    self.assertTrue("Permission denied" in output, msg="测试非root权限执行扩容脚本")


  def test_gpt_disk(self):
    """测试GPT格式的磁盘"""
    self._make_part("gpt")
    self.assertEqual(commands.getstatusoutput("mkfs.ext4 -F %s" % self.partition)[0], 0)
    self._part_probe()
    output = commands.getoutput("python devresize.py -f %s" % self.device)
    self.assertTrue("[ERROR] - Not support GPT disk currently" in output, msg="测试GPT格式的磁盘")


  # def run(self, result=None):
  #     """ Stop after first error """
  #     if not result.errors:
  #         super(TestDeviceResize, self).run(result)


if __name__ == "__main__":
  unittest.main()

  