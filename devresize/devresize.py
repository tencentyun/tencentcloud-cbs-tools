#!/usr/bin/env python2.7
# coding: utf-8
"""
It only handle the following two situations:
1. There is only one primary partiion in the disk with a format of ext2/3/4 or xfs;
2. The disk is raw with a file system whose format is ext2/3/4 or xfs.
"""

import struct
import array
import fcntl
import time
import sys
import os
import glob
import logging
import commands
import argparse
import atexit

BLKSSZGET = 0x1268
BLKGETSIZE = 0x1260
BLKRRPART = 0x125f
BLKGETSIZE64 = 0x80041272

logger = None


def read_ub(data):
    """read little-endian unsigned byte"""
    return struct.unpack('B', data[0])[0]


def read_us(data):
    """read little-endian unsigned short(2 bytes)"""
    return struct.unpack('<H', data[0:2])[0]


def read_ui(data):
    """read little-endian unsigned int(4 bytes)"""
    return struct.unpack('<I', data[0:4])[0]


def read_ul(data):
    """read little-endian unsigned long(8 bytes)"""
    return struct.unpack('<Q', data[0:8])[0]


def init_log():
    """初始化日志"""
    global logger
    log_file = 'devresize.log'
    fmt_file = '%(asctime)s - [%(levelname)-5.5s]- %(filename)s:%(lineno)s - %(message)s'
    fmt_stream = '[%(levelname)s] - %(message)s'
    logger = logging.getLogger('devresize')
    logger.setLevel(logging.DEBUG)

    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(fmt_file))
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.INFO)
    stream_handler.setFormatter(logging.Formatter(fmt_stream))
    logger.addHandler(stream_handler)


class PartitionEntry(object):
    """表示一个磁盘分区"""
    PartitionTypes = {
        0x05: "Microsoft Extended",
        0x83: "Linux",
        0x85: "Linux Extended"
    }

    def __init__(self, data):
        self.data = data
        self.boot_sig = data[0]

        self.start_head, self.start_sector, self.start_cylinder = (
            PartitionEntry.get_hsc(data[1:1 + 3]))

        self.partition_type = read_ub(data[4])

        self.end_head, self.end_sector, self.end_cylinder = (
            PartitionEntry.get_hsc(data[5:5 + 3]))

        self.start_lba = read_ui(data[8:8 + 4])
        self.sector_num = read_ui(data[12:12 + 4])

        self.partition_type_name = PartitionEntry.PartitionTypes.get(self.partition_type, "other")

    @staticmethod
    def get_hsc(data):
        """获取(head, sector, cylindar)"""
        h, s, c = struct.unpack('BBB', data[0:3])
        c = (c | ((s & 0xC0) << 2))
        s = (s & 0x3F)
        return h, s, c

    @staticmethod
    def cal_hsc(sector, hh, ss):
        """计算(head, sector, cylindar)"""
        s = sector % ss + 1
        sector /= ss
        h = sector % hh
        sector /= hh
        c = sector & 0xFF
        s |= (sector >> 2) & 0xC0
        return h, s, c

    def vaild_type(self):
        """校验分区类型是否在可处理的名单里"""
        return self.partition_type in self.PartitionTypes

    def isprimary(self):
        """是否为主分区"""
        return self.partition_type == 0x83

    def __str__(self):
        if not self.vaild_type():
            logger.info("%x" % self.partition_type)
            return "This isn't a Linux Partition!"
        return """
        Start h,s,c: %u %u %u
        End h,s,c: %u %u %u
        Partition Type Name:%s
        Start LBA: %u
        Sector Number: %u
        """ % (self.start_head, self.start_sector, self.start_cylinder,
               self.end_head, self.end_sector, self.end_cylinder,
               self.partition_type_name, self.start_lba, self.sector_num)


class MBR(object):
    def __init__(self, data):
        self.data = data
        self.boot_code = data[:446]
        self.mbr_sig = data[510:512]

        if self.check_mbr_sig():        # 如果存在分区
            self.partitions = ([PartitionEntry(data[446 + 16 * i:446 + 16 * (i + 1)])
                                for i in range(0, 4)])
        else:                           # 否则为裸盘文件系统
            self.partitions = None

        if self.partitions is not None:
            self.vaild_part_num = len(filter(lambda x: x.vaild_type(), self.partitions))
        else:
            self.vaild_part_num = 0

        self.device_heads = 0
        self.device_sectors = 0

        self.cal_device_hs()

    def cal_device_hs(self):
        """计算设备的heads和sectors"""
        if self.partitions is not None and self.vaild_part_num == 1:
            self.device_heads = self.partitions[0].end_head + 1
            self.device_sectors = self.partitions[0].end_sector & 0x3F

    def check_mbr_sig(self):
        """检查MBR签名"""
        mbr_sig = read_us(self.mbr_sig)
        if mbr_sig == 0xAA55:
            return True
        else:
            return False


def get_device_size(fd):
    """获取块设备大小"""
    buf = array.array('c', [chr(0)] * 8)
    fcntl.ioctl(fd, BLKSSZGET, buf, True)
    logical_sector_size = read_ul(buf)

    buf = array.array('c', [chr(0)] * 8)
    try:
        fcntl.ioctl(fd, BLKGETSIZE, buf, True)
        device_size = read_ul(buf) * 512
    except IOError:
        fcntl.ioctl(fd, BLKGETSIZE64, buf, True)
        device_size = read_ul(buf)
    device_sector_number = device_size / logical_sector_size
    logger.debug(
        """device_size:%d
        device_sector_number:%d
        logical_sector_size:%d""" % (device_size, device_sector_number, logical_sector_size))
    return device_size, device_sector_number, logical_sector_size


def is_ext_fs(fstype):
    return 'ext' in fstype


def check_fs_block_size(part, fstype, mount_dir):
    """获取文件系统块大小和块数"""
    if is_ext_fs(fstype):
        block_size = commands.getoutput("tune2fs -l %s | grep 'Block size' | awk '{print $3}'" % part)
        # block_count = commands.getoutput("tune2fs -l %s | grep 'Block count' | awk '{print $3}'" % part)
    else:
        mount_fs(part, mount_dir)
        output = commands.getoutput("xfs_info %s | grep '^data' | awk  -F '[= ,]+' '{print $3, $5}'" % part).split()
        umount_fs(part)
        block_size = output[0]
        # block_count = output[1]

    if not block_size:
        logger.error("Check filesystem %s block size error, cannot get block size." % part) 
        sys.exit(1)

    if int(block_size) != 4096:
        logger.error("Only can process filesystem with block size 4KB (actual block size is %s bytes)" % block_size)
        sys.exit(1)
    return mount_dir

def backup_mbr(part, data):
    """备份MBR元数据"""
    bak_name = '/tmp/MBR_%s_%s_bak' % (os.path.basename(part), time.strftime("%Y-%m-%d_%X", time.localtime()))
    bak_file = open(bak_name, 'w')
    bak_file.write(data)
    bak_file.close()
    logger.info("Backup MBR to %s" % bak_name)
    return bak_name


def cal_new_part(part_data, mbr, start_lab, new_end):
    """计算新的MBR分区"""
    device_heads, device_sectors = mbr.device_heads, mbr.device_sectors

    new_partition_sector_num = new_end - start_lab + 1
    begin_h, begin_s, begin_c = PartitionEntry.cal_hsc(start_lab, device_heads, device_sectors)
    end_h, end_s, end_c = PartitionEntry.cal_hsc(new_end, device_heads, device_sectors)

    new_part_data = list(part_data[:])
    new_part_data[1:1 + 3] = list(struct.pack('BBB', begin_h, begin_s, begin_c))
    new_part_data[5:5 + 3] = list(struct.pack('BBB', end_h, end_s, end_c))
    new_part_data[0xc:] = list(
        struct.pack('BBBB', (new_partition_sector_num & 0xff), ((new_partition_sector_num >> 8) & 0xff),
                    ((new_partition_sector_num >> 16) & 0xff), ((new_partition_sector_num >> 24) & 0xff)))

    logger.debug("""
    Start h,s,c: %u %u %u
    End h,s,c: %u %u %u
    Partition Type Name:%s
    Start LBA: %u
    Sector Number: %u
    """ % (begin_h, begin_s, begin_c,
           end_h, end_s, end_c,
           mbr.partitions[0].partition_type_name,
           mbr.partitions[0].start_lba,
           new_partition_sector_num))
    return new_part_data


def check_partition(dev, mbr):
    """检查磁盘分区"""
    resize_part_flag = True
    target_partition = ''

    part_count = int(commands.getoutput("ls %s* | wc -w" % dev)) - 1
    if part_count > 0 and part_count != mbr.vaild_part_num:
        logger.debug(commands.getoutput('ls %s*' % dev))
        logger.debug("%s != %s", part_count, mbr.vaild_part_num)
        logger.error("Disk %s has invalid partition" % dev)
        sys.exit(1)

    if mbr.vaild_part_num > 1:
        logger.error("Disk %s has multiple partitions." % dev)
        sys.exit(1)
    elif mbr.vaild_part_num == 1:  # only one partition, which is the primary partition
        if not mbr.partitions[0].isprimary():  # and the filesystem type is ext2/3/4.
            logger.error("Must be primary partition.")
            sys.exit(1)
        resize_part_flag = True
        if dev[-1].isdigit():
            target_partition = dev + 'p1'  # ex: /dev/nbd0 -> /dev/nbd0p1
        else:
            target_partition = dev + '1'  # ex: /dev/vdb -> /dev/vdb1
        logger.debug('target_partition:%s' % target_partition)
    elif mbr.vaild_part_num == 0:  # no partition but whole disk is ext2/3/4
        resize_part_flag = False
        target_partition = dev
    return target_partition, resize_part_flag


def check_format(part):
    """检查是否为支持的分区类型"""
    output = commands.getoutput('blkid %s' % part)
    if not output:
        logger.error("check filesystem format error, please ensure %s is a valid filesystem" % part)
        sys.exit(1)

    for fmt in ['ext2', 'ext3', 'ext4', 'xfs']:
        if fmt in output:
            return fmt
    logger.error("Only can process ext2/3/4 and xfs.")
    sys.exit(1)


def check_fs_healthy(part, fstype = 'ext'):
    """检查文件系统完整性"""
    logger.info("checking filesystem healthy")
    if is_ext_fs(fstype):
        ret = os.system('e2fsck -af %s' % part)
        logger.debug('e2fsck ret is %d' % ret)
        if ret == 1:
            logger.info('File system errors have been corrected')
        ret = ret not in [0, 1]
    else:
        ret = os.system('xfs_repair %s' % part)
        logger.debug('xfs_repair ret is %d' % ret)
    if ret:
        logger.error('File system %s error!' % part)
        sys.exit(1)


def mount_fs(part, mount_dir):
    """挂载块设备"""
    # first need to mount fs
    if not os.path.exists(mount_dir):
        os.mkdir(mount_dir)
    ret = os.system('mount %s %s' % (part, mount_dir))
    if ret != 0:
        raise RuntimeError('mount failed! (return code %s)' % ret)
    logger.info('mount %s %s' % (part, mount_dir))


def umount_fs(part):
    """解挂块设备"""
    mount_dir = commands.getoutput("mount | grep '%s ' | awk '{print $3}'" % part)
    if not mount_dir:   # if not mounted 
        return
    else:
        ret = os.system('umount %s' % part)
        logger.info('umount %s' % part)
        if ret != 0:
            raise RuntimeError('umount failed! (return code %s)' % ret)


def resize2fs(part):
    """使用resize2fs扩容ext文件系统"""
    logger.info("resize filesystem")
    ret = os.system('resize2fs -f %s' % part)
    logger.debug('resize2fs ret is %d' % ret)
    if ret != 0:
        raise RuntimeError('resize2fs failed! (return code %s)' % ret)


def resize_xfs(mount_dir):
    """扩容xfs文件系统"""
    logger.info("resize filesystem")
    ret = os.system('xfs_growfs %s' % mount_dir)
    logger.debug('xfs_growfs ret is %d' % ret)
    if ret != 0:
        raise RuntimeError('xfs_growfs failed! (return code %s)' % ret)


def check_mount(target_dev):  # target_dev is mounted!
    """确认要扩容的块设备未挂载"""
    output = commands.getoutput('mount | grep "%s "' % target_dev)
    if output:
        logger.error("Target partition %s must be unmounted." % target_dev)
        sys.exit(1)

def part_probe(fd):
    """将写入文件的数据落到磁盘上"""
    if logger:
        logger.debug('part_probe')
    fd.flush()
    time.sleep(1)
    fcntl.ioctl(fd, BLKRRPART)

def write_mbr(fd, mbr_data):
    """将mbr数据写入文件"""
    fd.seek(0)
    fd.write(mbr_data)
    time.sleep(1)
    part_probe(fd)
    time.sleep(1)


def check_permission(device):
    """检查设备访问权限"""
    if not os.access(device, os.W_OK):
        logger.error("Permission denied")
        sys.exit(1)


def check_args(device):
    """检查设备名最后一个字符是否为数字"""
    if device[-1].isdigit():
        logger.error("The argument should be a whole disk, not a partation! Example: %s" % get_disk_path(device))
        sys.exit(1)


def check_partition_need_resize(target_partition):
    """检查分区是否可扩容"""
    output = commands.getoutput("parted %s unit MiB print free | tail -n 2 | head -n 1" % target_partition)
    return "Free Space" in output
    

def check_mbr(device):
    """检查是否为mbr分区"""
    output = commands.getoutput("parted %s print | grep 'Partition Table'" % device)
    if 'gpt' in output:
        logger.error("Not support GPT disk currently")
        sys.exit(1)


def get_disk_path(partation_name):
    """从分区名解析出块设备名"""
    for i, ch in enumerate(os.path.basename(partation_name)[::-1]):
        if not ch.isdigit():
            return partation_name[::-1][i::][::-1]
    logger.error("invalid para %s" % partation_name)
    raise Exception("invalid para %s" % partation_name)


def closefd(fd):
    if not fd.closed:
        logger.debug("close fd")
        fd.close()


def main():
    """
    Steps:
        1. check filesystem format
        2. check unmounted
        3. check filesystem healthy
        4. check filesystem block size
        5. backup MBR
        6. rewrite MBR(resize partition)
        7. resize filesystem
    """
    init_log()
    logger.debug("user input:%s" % ' '.join(sys.argv))

    parser = argparse.ArgumentParser()
    parser.add_argument("device", help="your device path (not a partition)")
    parser.add_argument("-f", "--force", help="ignore all prompts", action="store_true")
    args = parser.parse_args()
    device = args.device
    
    check_args(device)

    check_permission(device)

    check_mbr(device)

    fd = open(device, 'r+')
    data = fd.read(512)
    mbr = MBR(data)
    bak_mbr_data = ''
    mount_dir = '/tmp/mount_point_%s_%s' % \
                (os.path.basename(device), time.strftime("%Y-%m-%d_%X", time.localtime()))
    atexit.register(closefd, fd)
    
    device_size, device_sector_number, logical_sector_size = get_device_size(fd)
    
    target_partition, resize_part_flag = check_partition(device, mbr)
    
    fstype = check_format(target_partition)

    check_mount(target_partition)
        
    check_fs_healthy(target_partition, fstype)

    check_fs_block_size(target_partition, fstype, mount_dir)

    if not args.force:
        user_input = raw_input("This operation will extend %s to the last sector of device. \n"
                            "To ensure the security of your valuable data, \n"
                            "please create a snapshot of this volume before resize its file system, continue? [Y/n]\n" % target_partition)
        if user_input.lower() != 'y' and user_input != '':
            logger.warn("User input neither 'y' nor '[Enter]',exit.")
            sys.exit(1)

    if not args.force:
        user_input = raw_input("It will resize (%s).\n"
                    "This operation may take from several minutes to several hours, continue? [Y/n]\n" % target_partition)
        if user_input.lower() != 'y' and user_input != '':
            logger.warn("User input neither 'y' nor '[Enter]',exit.")
            sys.exit(1)

    if resize_part_flag and check_partition_need_resize(device):   # if need to resize partition
        logger.debug("Begin to change the partation")
        if (mbr.partitions[0].start_lba + mbr.partitions[0].sector_num) == device_sector_number:
            logger.error("No free sectors available.")
            sys.exit(1)
        if mbr.partitions[0].sector_num > 0xFFFFFFFF * 512 / logical_sector_size:
            logger.error("Can't process the partition which have exceeded 2TB.")
            sys.exit(1)
        new_start_sector = mbr.partitions[0].start_lba
        new_end_sector = device_sector_number - 1
        if (new_end_sector - new_start_sector + 1) * logical_sector_size > 0xFFFFFFFF * 512:
            if not args.force:
                user_input = raw_input("The size of this disk is %.2fTB (%d bytes).\n"
                    "But DOS partition table format can not be used on drives for volumes "
                    "larger than 2TB (2199023255040 bytes).\n"
                    "Do you want to resize (%s) to 2TB? [Y/n]\n"
                    % (round(device_size / 1024.0 / 1024 / 1024 / 1024, 2), device_size,
                        target_partition))
                if user_input.lower() != 'y' and user_input != '':
                    logger.warn("User input neither 'y' nor '[Enter]',exit.")
                    sys.exit(1)
            new_end_sector = 0xFFFFFFFF * 512 / logical_sector_size + new_start_sector - 1

        new_mbr_data = list(data)[:]
        new_mbr_data[446:446 + 16] = cal_new_part(data[446:446 + 16], mbr,
                                                new_start_sector, new_end_sector)
        backup_mbr(target_partition, data)
        bak_mbr_data = data
    else:
        logger.info("No need to resize partition, try to resize filesystem")
        resize_part_flag = False

    time.sleep(1)
    # rewrite MBR(if necessary), resize file system
    try:
        if resize_part_flag:
            umount_fs(target_partition)
            write_mbr(fd, ''.join(new_mbr_data))

        umount_fs(target_partition)
        if is_ext_fs(fstype):
            resize2fs(target_partition)
        else:
            mount_fs(target_partition, mount_dir)
            resize_xfs(mount_dir)
            umount_fs(target_partition)
    except Exception, e:
        umount_fs(target_partition)
        logger.error(e)
        # logger.error('Some error occurred! Please make sure the e2fsprogs version is above 1.42.13.')
        logger.error('Some error occurred! Maybe you should call the customer service staff.')
        if resize_part_flag:
            logger.error('Resize filesystem aborted, restore MBR')
            write_mbr(fd, bak_mbr_data)
        sys.exit(1)
    logger.info("Finished")



if __name__ == '__main__':
    main()


